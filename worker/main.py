import os
import json
import logging
import time

import redis
import openai
import chromadb
from dotenv import load_dotenv
from githubkit import GitHub
from githubkit.auth import AppInstallationAuthStrategy

# CORRECTED IMPORT: Use the new dedicated package for Chroma
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

# --- Configuration ---
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - WORKER - %(levelname)s - %(message)s')

APP_ID = os.getenv("APP_ID")
PRIVATE_KEY_PATH = os.getenv("PRIVATE_KEY_PATH")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
JOB_QUEUE_NAME = os.getenv("JOB_QUEUE_NAME", "pr_review_jobs")

# ChromaDB/RAG Config
CHROMA_HOST = "localhost"
CHROMA_PORT = "8000"
CHROMA_COLLECTION_NAME = "codescribe_rules"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# --- Validation ---
if not all([APP_ID, PRIVATE_KEY_PATH, OPENAI_API_KEY]):
    logging.error("APP_ID, PRIVATE_KEY_PATH, and OPENAI_API_KEY must be set.")
    exit(1)

try:
    with open(PRIVATE_KEY_PATH, 'r') as f:
        PRIVATE_KEY = f.read()
except FileNotFoundError:
    logging.error(f"Private key file not found at: {PRIVATE_KEY_PATH}")
    exit(1)

# --- GitHub Authentication ---
def get_installation_client(installation_id: int) -> GitHub:
    auth_strategy = AppInstallationAuthStrategy(app_id=APP_ID, private_key=PRIVATE_KEY, installation_id=installation_id)
    return GitHub(auth_strategy)

# --- AI Review Logic with RAG ---
def get_rag_review_chain():
    """Initializes and returns the complete RAG chain for code reviews."""
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)
    chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    
    vectorstore = Chroma(
        client=chroma_client,
        collection_name=CHROMA_COLLECTION_NAME,
        embedding_function=embeddings,
    )
    retriever = vectorstore.as_retriever()
    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.5)

    template = """
    You are an expert code reviewer AI named CodeScribe.
    Your analysis must be based on the following internal coding standards.

    **Internal Standards:**
    {context}

    ---

    **Pull Request Details:**
    Review the following pull request based *only* on the standards provided above.
    Identify any violations of these standards. If there are no violations, simply state that the code adheres to the standards.

    **PR Title:** {pr_title}
    **PR Description:** {pr_description}
    **Code Diff:**
    ```diff
    {diff}
    ```
    """
    prompt = ChatPromptTemplate.from_template(template)

    rag_chain = (
        {
            "context": (lambda x: x['diff']) | retriever,
            "diff": (lambda x: x['diff']),
            "pr_title": (lambda x: x['pr_title']),
            "pr_description": (lambda x: x['pr_description']),
        }
        | prompt
        | llm
        | StrOutputParser()
    )
    return rag_chain

# --- Main Worker Logic ---
def process_jobs():
    logging.info("Worker started with RAG support. Waiting for jobs...")
    rag_chain = get_rag_review_chain() # Initialize the chain once
    
    try:
        redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
        redis_client.ping()
        logging.info(f"Successfully connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
    except redis.exceptions.ConnectionError as e:
        logging.error(f"Could not connect to Redis: {e}")
        return

    while True:
        try:
            _, job_json = redis_client.brpop(JOB_QUEUE_NAME, timeout=0)
            job_data = json.loads(job_json)
            logging.info(f"Processing RAG job: {job_data}")
            
            repo_full_name = job_data["repo_full_name"]
            pr_number = job_data["pr_number"]
            installation_id = job_data["installation_id"]
            owner, repo = repo_full_name.split('/')
            
            github_client = get_installation_client(installation_id)
            
            diff_response = github_client.rest.pulls.get(
                owner=owner, repo=repo, pull_number=pr_number,
                headers={"Accept": "application/vnd.github.v3.diff"}
            )
            pr_diff = diff_response.content.decode('utf-8')

            pr_details_response = github_client.rest.pulls.get(
                owner=owner, repo=repo, pull_number=pr_number
            )
            pr_title = pr_details_response.parsed_data.title
            pr_body = pr_details_response.parsed_data.body or ""
            
            logging.info("Invoking RAG chain for review...")
            review_comment = rag_chain.invoke({
                "diff": pr_diff,
                "pr_title": pr_title,
                "pr_description": pr_body
            })
            logging.info("RAG chain finished. Posting review.")

            github_client.rest.issues.create_comment(
                owner=owner, repo=repo, issue_number=pr_number,
                body=review_comment
            )
            
            logging.info(f"Successfully posted RAG-based review on PR #{pr_number}")

        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}", exc_info=True)
            if 'job_json' in locals() and redis_client:
                redis_client.lpush(JOB_QUEUE_NAME, job_json)
            time.sleep(5)

if __name__ == "__main__":
    process_jobs()