import os
import json
import boto3
import logging
from urllib.parse import unquote_plus

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment variables
DB_HOST = os.getenv("DB_HOST",)
DB_PORT = os.getenv("DB_PORT")
DB_USER = os.getenv("DB_USER")
DB_NAME = os.getenv("DB_NAME")
DB_REGION = os.getenv("DB_REGION")
TABLE_NAME = os.getenv("TABLE_NAME")
S3_BUCKET = os.getenv("S3_BUCKET")
S3_PREFIX = os.getenv("S3_PREFIX", "tavily_search_results/")

def get_db_connection():
    try:
        import psycopg2
    except ImportError:
        logger.error("psycopg2 not found. Ensure it is included in the deployment package.")
        raise

    if not DB_HOST or not DB_USER:
        raise ValueError("DB_HOST or DB_USER not set.")

    # Generate RDS IAM auth token
    rds = boto3.client("rds", region_name=DB_REGION)
    try:
        token = rds.generate_db_auth_token(
            DBHostname=DB_HOST,
            Port=DB_PORT,
            DBUsername=DB_USER,
        )
    except Exception as e:
        logger.error(f"Failed to generate DB auth token: {e}")
        raise

    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=DB_USER,
            password=token,
            dbname=DB_NAME,
            sslmode="require"
        )
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to DB: {e}")
        raise

def ensure_table_exists(conn):
    with conn.cursor() as cur:
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                id SERIAL PRIMARY KEY,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                content TEXT ,
                source VARCHAR(255) NOT NULL,
                score FLOAT,
                published_date TIMESTAMP,
                searched_at TIMESTAMP,
                individuals_mentioned TEXT[],
                keywords_used TEXT[],
                UNIQUE(url)
            );
        """)
        # Explicitly try to add column if table exists but column does not
        try:
            cur.execute(f"""
                DO $
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='{TABLE_NAME}' AND column_name='source') THEN
                        ALTER TABLE {TABLE_NAME} ADD COLUMN source VARCHAR(255);
                    END IF;
                END $;
            """)
        except Exception as e:
            logger.warning(f"Could not add source column: {e}")
            
        conn.commit()

def insert_into_db(conn, processed_data):
    """
    Insert processed data into Aurora DB.
    """
    query = f"""
        INSERT INTO {TABLE_NAME} (
            url, title, content, source, published_date, searched_at, individuals_mentioned, keywords_used, score
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (url) DO UPDATE SET
            searched_at = EXCLUDED.searched_at,
            content = EXCLUDED.content,
            individuals_mentioned = EXCLUDED.individuals_mentioned,
            keywords_used = EXCLUDED.keywords_used,
            score = EXCLUDED.score,
            source = EXCLUDED.source
    """
    
    with conn.cursor() as cur:
        cur.execute(query, (
            processed_data.get("url"),
            processed_data.get("title"),
            processed_data.get("content"),
            processed_data.get("source"),
            processed_data.get("published_date"),
            processed_data.get("searched_at"),
            processed_data.get("individuals_mentioned"),
            processed_data.get("keywords_used"),
            processed_data.get("score"),
        ))
    conn.commit()



def get_latest_s3_key(conn , individual):
    """
    Queries the DB to find the lexicographically largest s3_key processed so far.
    Only considers keys that match the new date-based structure (starting with digits after prefix)
    to avoid getting stuck behind old legacy keys (which start with letters and sort after numbers).
    """
    try:
       
        query = f"SELECT MAX(scanned_at) FROM {TABLE_NAME} WHERE individuals_mentioned @> ARRAY['{individual}']"
        with conn.cursor() as cur:
            cur.execute(query)
            result = cur.fetchone()
            if result and result[0]:
                date = result[0].strftime("%Y%m%d")
                return date
    except Exception as e:
        logger.warning(f"Could not fetch latest s3_key: {e}")
    return None

def fetch_new_files(s3_client, bucket, last_key):
    """
    Lists objects in S3 that are lexicographically greater than last_key.
    Filters out keys that do not match the expected date-based structure.
    """
    paginator = s3_client.get_paginator('list_objects_v2')
    
    kwargs = {'Bucket': bucket, 'Prefix': S3_PREFIX}
    
    if last_key:
        # StartAfter is strictly greater than
        kwargs['StartAfter'] = last_key

    for page in paginator.paginate(**kwargs):
        if 'Contents' in page:
            for obj in page['Contents']:
                key = obj['Key']
            

                # Ensure we only process keys strictly greater than last_key
                # (StartAfter handles this, but extra safety check)
                if last_key and key <= last_key:
                    continue
                yield key

def lambda_handler(event, context):
    s3 = boto3.client("s3")
    name = event.get("name")
    key = event.get("key")
    
    # Handle EventBridge S3 Object Created Event
    if not key and 'detail' in event and 'object' in event.get('detail', {}):
        try:
            key = event['detail']['object']['key']
            logger.info(f"Received EventBridge trigger for key: {key}")
        except KeyError:
            logger.warning("EventBridge event structure unexpected: missing object key")

    # Handle Direct S3 Event Notification (Records)
    if not key and 'Records' in event:
        try:
            key = unquote_plus(event['Records'][0]['s3']['object']['key'])
            logger.info(f"Received S3 Event trigger for key: {key}")
        except (KeyError, IndexError):
            logger.warning("S3 Event structure unexpected")
    
    if key:
        parts = key.split('/')
        if len(parts) >= 3: 
            name = parts[1]

    if name:
        if "_" in name:
            name    = " ".join(name.split("_"))
        

    if not name:
        logger.error("No name provided in event")
        return {
            "statusCode": 400,
            "body": json.dumps({
                "error": "No name provided in event"
            })
        }

    conn = None

    try:
        conn = get_db_connection()
        ensure_table_exists(conn)

        # 1. Get the latest processed key from DB
        last_key = get_latest_s3_key(conn , name)
        last_key = f"{name}/{last_key}/"
        logger.info(f"Last processed key: {last_key}")

        # 2. Fetch new files from S3 starting after last_key
        processed_count = 0
        for key in fetch_new_files(s3, S3_BUCKET, last_key):
            logger.info(f"Processing file: {key}")
            
            try:
                response = s3.get_object(Bucket=S3_BUCKET, Key=key)
                content = response['Body'].read().decode('utf-8')
                data = json.loads(content)
                
                if isinstance(data, list):
                    for record in data:
                
                        insert_into_db(conn, record)
                else:
                    insert_into_db(conn, data)
                
                processed_count += 1
                logger.info(f"Successfully processed {len(data) if isinstance(data, list) else 1} records from {key}")

            except Exception as e:
                logger.error(f"Failed to process record {key}: {e}")
                continue
        
        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Processing complete", 
                "processed_files": processed_count
            })
        }

    except Exception as e:
        logger.error(f"Loader execution failed: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
    finally:
        if conn:
            conn.close()
