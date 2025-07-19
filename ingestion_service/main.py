import os
import hmac
import hashlib
import json
import logging

import redis
from fastapi import FastAPI, Request, Header, HTTPException, status
from dotenv import load_dotenv

# --- Configuration ---
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - INGESTION - %(levelname)s - %(message)s')

GITHUB_WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
JOB_QUEUE_NAME = os.getenv("JOB_QUEUE_NAME", "pr_review_jobs")
REPLY_QUEUE_NAME = "pr_reply_jobs"


# --- Application Setup ---
app = FastAPI()

try:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0, decode_responses=True)
    redis_client.ping()
    logging.info(f"Successfully connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
except redis.exceptions.ConnectionError as e:
    logging.error(f"Could not connect to Redis: {e}")
    redis_client = None


# --- Security & Validation (Corrected) ---
async def verify_signature(request: Request):
    if not GITHUB_WEBHOOK_SECRET:
        logging.error("WEBHOOK_SECRET is not configured. Cannot verify signatures.")
        return

    signature_header = request.headers.get('X-Hub-Signature-256')
    if not signature_header:
        logging.warning("Request received without X-Hub-Signature-256 header.")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing signature")

    hash_algorithm, signature = signature_header.split('=', 1)
    if hash_algorithm != 'sha256':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported signature algorithm")

    body = await request.body()
    
    # CORRECTED VARIABLE NAME HERE
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

    try:
        # --- Event Routing Logic ---
        if x_github_event == "pull_request":
            action = payload.get("action")
            if action in ["opened", "synchronize"]:
                job_data = {
                    "event_type": "pull_request",
                    "repo_full_name": payload["repository"]["full_name"],
                    "pr_number": payload["pull_request"]["number"],
                    "installation_id": payload["installation"]["id"],
                }
                redis_client.lpush(JOB_QUEUE_NAME, json.dumps(job_data))
                logging.info(f"Queued job for PR #{job_data['pr_number']} in repo '{job_data['repo_full_name']}'")
                return {"status": "success", "message": f"Job queued for PR #{job_data['pr_number']}"}
        
        elif x_github_event == "issue_comment":
            action = payload.get("action")
            if action == "created":
                # SIMPLIFIED LOGIC: Process any comment on a PR
                if "pull_request" in payload["issue"]:
                    job_data = {
                        "event_type": "issue_comment",
                        "repo_full_name": payload["repository"]["full_name"],
                        "pr_number": payload["issue"]["number"],
                        "installation_id": payload["installation"]["id"],
                        "comment_body": payload["comment"]["body"],
                        "commenter_login": payload["comment"]["user"]["login"],
                    }
                    redis_client.lpush(REPLY_QUEUE_NAME, json.dumps(job_data))
                    logging.info(f"Queued reply job for PR #{job_data['pr_number']}")
                    return {"status": "success", "message": "Reply job queued"}

    except KeyError as e:
        logging.error(f"Missing expected key in {x_github_event} payload: {e}")
        return {"status": "error", "message": f"Malformed payload, missing {e}"}

    return {"status": "success", "message": "Event received but not processed"}