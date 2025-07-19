import os
import json
import logging
import time
import ast
import base64 # Import the standard base64 library

import redis
import openai
import chromadb
from dotenv import load_dotenv
from githubkit import GitHub
from githubkit.auth import AppInstallationAuthStrategy

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers.openai_tools import PydanticToolsParser
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain_openai import ChatOpenAI


# --- Configuration and Validation (remains the same) ---
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
load_dotenv(dotenv_path=dotenv_path)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - WORKER - %(levelname)s - %(message)s')
APP_ID = os.getenv("APP_ID")
PRIVATE_KEY_PATH = os.getenv("PRIVATE_KEY_PATH")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
JOB_QUEUE_NAME = os.getenv("JOB_QUEUE_NAME", "pr_review_jobs")
CHROMA_HOST = "localhost"
CHROMA_PORT = "8000"
CHROMA_COLLECTION_NAME = "codescribe_rules"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
if not all([APP_ID, PRIVATE_KEY_PATH, OPENAI_API_KEY]):
    exit(1)
try:
    with open(PRIVATE_KEY_PATH, 'r') as f:
        PRIVATE_KEY = f.read()
except FileNotFoundError:
    exit(1)


# --- GitHub, Tool, and AI Chain Setup (remains the same) ---
def get_installation_client(installation_id: int) -> GitHub:
    auth_strategy = AppInstallationAuthStrategy(app_id=APP_ID, private_key=PRIVATE_KEY, installation_id=installation_id)
    return GitHub(auth_strategy)

class CodeSuggestion(BaseModel):
    description: str = Field(description="A clear and concise description of the issue found and why it violates the standards.")
    suggestion: str = Field(description="The complete, corrected code block to be suggested.")

def get_review_chain():
    embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)
    chroma_client = chromadb.HttpClient(host=CHROMA_HOST, port=CHROMA_PORT)
    vectorstore = Chroma(client=chroma_client, collection_name=CHROMA_COLLECTION_NAME, embedding_function=embeddings)
    retriever = vectorstore.as_retriever()
    llm = ChatOpenAI(model="gpt-4o", temperature=0.5)
    llm_with_tools = llm.bind_tools([CodeSuggestion])
    template = """
    You are an expert code reviewer AI named CodeScribe.
    Your analysis must be based on the following internal coding standards.

    **Internal Standards:**
    {context}

    ---

    **Pull Request Details:**
    Review the following pull request based *only* on the standards provided above.
    For each violation you find, you MUST call the `CodeSuggestion` tool with a description of the issue and the corrected code.
    If you find multiple violations, call the tool multiple times.

    **PR Title:** {pr_title}
    **PR Description:** {pr_description}
    **Code Diff:**
    ```diff
    {diff}
    ```
    """
    prompt = ChatPromptTemplate.from_template(template)
    output_parser = PydanticToolsParser(tools=[CodeSuggestion])
    chain = (
        {
            "context": (lambda x: x['diff']) | retriever,
            "diff": (lambda x: x['diff']),
            "pr_title": (lambda x: x['pr_title']),
            "pr_description": (lambda x: x['pr_description']),
        }
        | prompt
        | llm_with_tools
        | output_parser
    )
    return chain

# --- NEW: AST Processing Function ---
def analyze_python_file_with_ast(file_content: str):
    """Parses a Python file's content into an AST and logs it."""
    logging.info("--- Starting AST Analysis ---")
    try:
        # 1. Parse the file content into an AST
        tree = ast.parse(file_content)
        logging.info("Successfully parsed file into an AST.")
        
        # 2. Log the AST structure for inspection
        logging.info(f"AST Dump:\n{ast.dump(tree, indent=4)}")

        # 3. Unparse the AST back into source code
        reconstructed_code = ast.unparse(tree)
        logging.info("Successfully unparsed AST back to source code.")
        logging.info(f"Reconstructed Code:\n---\n{reconstructed_code}\n---")
        
    except SyntaxError as e:
        logging.error(f"Could not parse file due to a syntax error: {e}")
    except Exception as e:
        logging.error(f"An unexpected error occurred during AST analysis: {e}", exc_info=True)
    logging.info("--- Finished AST Analysis ---")


# --- Main Worker Logic ---
def process_jobs():
    logging.info("Worker started with AST support. Waiting for jobs...")
    review_chain = get_review_chain()
    
    try:
        redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
        redis_client.ping()
        logging.info(f"Successfully connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
    except redis.exceptions.ConnectionError as e:
        return

    while True:
        try:
            _, job_json = redis_client.brpop(JOB_QUEUE_NAME, timeout=0)
            job_data = json.loads(job_json)
            logging.info(f"Processing AST job: {job_data}")
            
            repo_full_name, pr_number, installation_id = job_data["repo_full_name"], job_data["pr_number"], job_data["installation_id"]
            owner, repo = repo_full_name.split('/')
            
            github_client = get_installation_client(installation_id)
            
            files_in_pr_response = github_client.rest.pulls.list_files(owner=owner, repo=repo, pull_number=pr_number)
            for file in files_in_pr_response.parsed_data:
                if file.filename.endswith(".py"):
                    logging.info(f"Found Python file: {file.filename}")
                    
                    pr_details = github_client.rest.pulls.get(owner=owner, repo=repo, pull_number=pr_number).parsed_data
                    head_branch_ref = pr_details.head.ref
                    
                    content_response = github_client.rest.repos.get_content(
                        owner=owner,
                        repo=repo,
                        path=file.filename,
                        ref=head_branch_ref
                    )
                    # CORRECTED LINE: Access .content and use the base64 library
                    base64_content = content_response.parsed_data.content
                    decoded_bytes = base64.b64decode(base64_content)
                    file_content = decoded_bytes.decode('utf-8')
                    
                    analyze_python_file_with_ast(file_content)

            logging.info("Proceeding with standard review process...")
            diff_response = github_client.rest.pulls.get(owner=owner, repo=repo, pull_number=pr_number, headers={"Accept": "application/vnd.github.v3.diff"})
            pr_diff = diff_response.content.decode('utf-8')
            pr_details_response = github_client.rest.pulls.get(owner=owner, repo=repo, pull_number=pr_number)
            pr_title = pr_details_response.parsed_data.title
            pr_body = pr_details_response.parsed_data.body or ""
            
            suggestions = review_chain.invoke({"diff": pr_diff, "pr_title": pr_title, "pr_description": pr_body})
            
            if not suggestions:
                review_body = "Great work! I analyzed the code and it adheres to all our project's coding standards."
            else:
                comment_parts = ["I've identified the following areas for improvement based on our coding standards:"]
                for i, suggestion in enumerate(suggestions):
                    comment_parts.append(f"\n**{i+1}. {suggestion.description}**\n")
                    comment_parts.append(f"```suggestion\n{suggestion.suggestion}\n```")
                review_body = "\n".join(comment_parts)
            
            github_client.rest.issues.create_comment(owner=owner, repo=repo, issue_number=pr_number, body=review_body)
            logging.info(f"Successfully posted structured review on PR #{pr_number}")

        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}", exc_info=True)
            if 'job_json' in locals() and redis_client:
                redis_client.lpush(JOB_QUEUE_NAME, job_json)
            time.sleep(5)

if __name__ == "__main__":
    process_jobs()