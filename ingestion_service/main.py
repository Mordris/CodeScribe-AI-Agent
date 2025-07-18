import os
import hmac
import hashlib
import json
import logging

import redis
from fastapi import FastAPI, Request, Header, HTTPException, status
from dotenv import load_dotenv

# --- Configuration ---
# Load environment variables from .env file at the project root
# We construct the path relative to this file's location.
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Get secrets and configuration from environment variables
GITHUB_WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
JOB_QUEUE_NAME = os.getenv("JOB_QUEUE_NAME", "pr_review_jobs")

# --- Application Setup ---
app = FastAPI()

# Connect to Redis
try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
    # Ping the server to check the connection
    redis_client.ping()
    logging.info(f"Successfully connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
except redis.exceptions.ConnectionError as e:
    logging.error(f"Could not connect to Redis: {e}")
    redis_client = None

# --- Security & Validation ---
async def verify_signature(request: Request):
    """
    Verify that the incoming request is a genuine GitHub webhook.
    """
    if not GITHUB_WEBHOOK_SECRET:
        logging.error("WEBHOOK_SECRET is not configured. Cannot verify signatures.")
        # In a production environment, you might want to deny all requests if the secret isn't set.
        # For local dev, we can allow it to proceed but log a severe warning.
        return

    signature_header = request.headers.get('X-Hub-Signature-256')
    if not signature_header:
        logging.warning("Request received without X-Hub-Signature-256 header.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing signature")

    # The header is in the format `sha256=...`
    hash_algorithm, signature = signature_header.split('=', 1)
    if hash_algorithm != 'sha256':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported signature algorithm")

    # We need the raw request body to compute the hash
    body = await request.body()
    
    # Compute the expected signature
    expected_signature = hmac.new(
        key=GITHUB_WEBHOOK_SECRET.encode('utf-8'),
        msg=body,
        digestmod=hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        logging.error("Signature mismatch. Request may be fraudulent.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid signature")

# --- Webhook Endpoint ---
@app.post("/webhook")
async def github_webhook(request: Request, x_github_event: str = Header(None)):
    """
    The main webhook endpoint that receives events from GitHub.
    """
    await verify_signature(request)
    
    if not redis_client:
        logging.error("Redis client is not available. Cannot queue job.")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis connection failed")

    payload = await request.json()
    
    logging.info(f"Received GitHub event: '{x_github_event}'")

    # We only care about pull requests that are opened or resynchronized
    if x_github_event == "pull_request":
        action = payload.get("action")
        if action in ["opened", "synchronize"]:
            try:
                # Extract the necessary information from the payload
                repo_full_name = payload["repository"]["full_name"]
                pr_number = payload["pull_request"]["number"]
                installation_id = payload["installation"]["id"]
                
                # Create a job dictionary
                job_data = {
                    "repo_full_name": repo_full_name,
                    "pr_number": pr_number,
                    "installation_id": installation_id,
                }
                
                # Push the job to the Redis queue
                redis_client.lpush(JOB_QUEUE_NAME, json.dumps(job_data))
                logging.info(f"Queued job for PR #{pr_number} in repo '{repo_full_name}'")
                
                return {"status": "success", "message": f"Job queued for PR #{pr_number}"}
            except KeyError as e:
                logging.error(f"Missing expected key in pull_request payload: {e}")
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Malformed payload: missing {e}")

    return {"status": "success", "message": "Event received but not processed"}