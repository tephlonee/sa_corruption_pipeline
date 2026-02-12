import os
import json
import boto3
import logging
import re
from urllib.parse import urlparse
from datetime import datetime
from tavily import TavilyClient

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
S3_BUCKET = os.getenv("S3_BUCKET")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
MAX_RESULTS = int(os.getenv("MAX_RESULTS", 20))

KEYWORDS = [
    "Corruption", "bribery", "fraud", "graft", "embezzlement", "wasteful spending", "misuse of public funds", "financial misconduct", 
]

INCLUDE_DOMAINS = [
    "news24.com",
    "timeslive.co.za",
    "mg.co.za",
    "iol.co.za",
    "fin24.com",
    "dailymaverick.com"
]

def extract_domain(url):
    """
    Extracts the domain (source) from a URL.
    """
    if not url:
        return None
    try:
        parsed_uri = urlparse(url)
        domain = parsed_uri.netloc
        # Optional: remove 'www.' if preferred
        if domain.startswith("www."):
            domain = domain[4:]
        return domain
    except Exception:
        return None

def process_content(result_item, query, searched_at, individual, keywords):
    """
    Process a single Tavily result item.
    """
    content = result_item.get("content", "")
    title = result_item.get("title", "")
    url = result_item.get("url", "")
    published_date_str = result_item.get("published_date")
    
    # Parse published_date
    published_date = None
    if published_date_str:
        try:
            # Handle potential 'Z' or other formats
            if published_date_str.endswith('Z'):
                published_date_str = published_date_str[:-1] + '+00:00'
            published_date = datetime.fromisoformat(published_date_str)
        except ValueError:
            logger.warning(f"Could not parse published_date: {published_date_str}")
    else:
        published_date = searched_at

    # Extract names
    mentioned_names = None
    if individual:
        mentioned_names = [individual]
    
    # Extract source/domain
    source = extract_domain(url)
    
    return {
        "url": url,
        "title": title,
        "content": content,
        "source": source,
        "published_date": published_date, # datetime object
        "searched_at": searched_at,       # datetime object
        "individuals_mentioned": mentioned_names,
        "keywords_used": keywords,
        "score": result_item.get("score"),
    }

def save_to_s3(s3_client, bucket, data_list, timestamp , individual):
    """
    Save the list of processed data to S3 as a single JSON file.
    Returns the S3 key.
    """
    # Create a safe filename from query and timestamp
    # We use the individual's name for the folder structure
    date_str = timestamp.date().strftime("%Y%m%d")
    timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S")

    # Clean individual name for S3 path
    safe_individual = re.sub(r'[^a-zA-Z0-9]', '_', individual)

    key = f"tavily_search_results/{safe_individual}/{date_str}/{timestamp_str}.json"
    
    try:
        # Serialize datetime objects for JSON
        json_data = json.dumps(data_list, default=str)
        s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=json_data,
            ContentType='application/json'
        )
        return key
    except Exception as e:
        logger.error(f"Failed to save to S3: {e}")
        return None

def lambda_handler(event, context):
    individual = event.get("name")
    keywords = event.get("keywords", KEYWORDS)
    include_domains = event.get("include_domains", INCLUDE_DOMAINS)

    if not individual:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing 'name' parameter"})
        }

    if isinstance(keywords, list):
        keywords = [k.strip() for k in keywords if k.strip()]
    
    else:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Keywords must be a list of strings"})
        }
    
    query = f"{', '.join(keywords)} and fraud related news involving {individual} in South Africa"

    if not TAVILY_API_KEY:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "TAVILY_API_KEY not set"})
        }

    s3 = boto3.client("s3")
    
    try:
        # Initialize Tavily
        tavily = TavilyClient(api_key=TAVILY_API_KEY)
        
        # Perform Search
        logger.info(f"Searching for: {query}")
        search_result = tavily.search(
            query=query, search_depth="advanced", 
            include_domains=include_domains,
            max_results=MAX_RESULTS
        )

        results = search_result.get("results", [])
        
        searched_at = datetime.utcnow()
        processed_items = []
        
        for item in results:
            # Process content
            processed_item = process_content(item, query, searched_at , individual, keywords)
            processed_items.append(processed_item)
            
        # Save all items to S3 in one go
        if processed_items:
            save_to_s3(s3, S3_BUCKET, processed_items, searched_at , individual)
            
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Search completed and data saved to S3.",
                "processed_count": len(processed_items),
                "query": query
            })
        }

    except Exception as e:
        logger.error(f"Lambda execution failed: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }

if __name__ == "__main__":
    # Local testing
    # os.environ["TAVILY_API_KEY"] = "tvly-..."
    print(lambda_handler({"name": "Senzo Mchunu"}, {}))
