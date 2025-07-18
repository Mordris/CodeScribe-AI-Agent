import os
import json
import logging
import time

import redis
from dotenv import load_dotenv
from githubkit import GitHub
# Correctly import the specific authentication class we need
from githubkit.auth import AppInstallationAuthStrategy

# --- Configuration ---
# Load environment variables from .env file at the project root
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - WORKER - %(levelname)s - %(message)s')

# Get configuration from environment variables
APP_ID = os.getenv("APP_ID")
PRIVATE_KEY_PATH = os.getenv("PRIVATE_KEY_PATH")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
JOB_QUEUE_NAME = os.getenv("JOB_QUEUE_NAME", "pr_review_jobs")

# --- Validation ---
if not all([APP_ID, PRIVATE_KEY_PATH]):
    logging.error("APP_ID and PRIVATE_KEY_PATH must be set in the environment.")
    exit(1)

try:
    with open(PRIVATE_KEY_PATH, 'r') as f:
        PRIVATE_KEY = f.read()
except FileNotFoundError:
    logging.error(f"Private key file not found at: {PRIVATE_KEY_PATH}")
    exit(1)

# --- GitHub Authentication (Corrected) ---
def get_installation_client(installation_id: int) -> GitHub:
    """Authenticates as a specific installation of the GitHub App."""
    # Use the correct strategy class that accepts the installation_id
    auth_strategy = AppInstallationAuthStrategy(
        app_id=APP_ID,
        private_key=PRIVATE_KEY,
        installation_id=installation_id,
    )
    return GitHub(auth_strategy)

# --- Main Worker Logic ---
def process_jobs():
    """Continuously listens for jobs on the Redis queue and processes them."""
    logging.info("Worker restarted. Waiting for jobs...")
    
    # Connect to Redis
    try:
        redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
        redis_client.ping()
        logging.info(f"Successfully connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
    except redis.exceptions.ConnectionError as e:
        logging.error(f"Could not connect to Redis: {e}")
        return # Exit if we can't connect to Redis

    while True:
        try:
            # BRPOP is a blocking pop. It waits until a job is available.
            # The '0' means it will wait indefinitely.
            _, job_json = redis_client.brpop(JOB_QUEUE_NAME, timeout=0)
            
            # The job failed before, so it was put back on the queue. We'll re-process it.
            job_data = json.loads(job_json)
            logging.info(f"Processing job: {job_data}")
            
            repo_full_name = job_data["repo_full_name"]
            pr_number = job_data["pr_number"]
            installation_id = job_data["installation_id"]
            
            owner, repo = repo_full_name.split('/')
            
            # 1. Get an authenticated client for the specific installation
            github_client = get_installation_client(installation_id)
            
            # 2. Post a comment on the pull request
            comment_body = "Hello from CodeScribe! I am preparing to review this PR."
            
            github_client.rest.issues.create_comment(
                owner=owner,
                repo=repo,
                issue_number=pr_number,
                body=comment_body,
            )
            
            logging.info(f"Successfully posted comment on PR #{pr_number} in {repo_full_name}")

        except json.JSONDecodeError:
            logging.error(f"Could not decode job data: {job_json}")
            redis_client.lpush(JOB_QUEUE_NAME, job_json) # Re-queue the job
        except KeyError as e:
            logging.error(f"Job data missing expected key: {e}")
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            # Re-queue the job so we can try again after fixing the issue
            if 'job_json' in locals():
                redis_client.lpush(JOB_QUEUE_NAME, job_json)
            # Wait for a moment before trying again to prevent rapid-fire failures
            time.sleep(5)


if __name__ == "__main__":
    process_jobs()