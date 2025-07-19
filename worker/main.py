import os
import json
import logging
import time
import ast
import base64

import redis
import openai
import chromadb
from dotenv import load_dotenv
from githubkit import GitHub
from githubkit.auth import AppInstallationAuthStrategy

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers.openai_tools import PydanticToolsParser
from langchain_core.pydantic_v1 import BaseModel, Field
from langchain_openai import ChatOpenAI

# --- Configuration ---
load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - WORKER - %(levelname)s - %(message)s')

APP_ID = os.getenv("APP_ID")
BOT_NAME = "codescribe-mordris[bot]"
PRIVATE_KEY_PATH = os.getenv("PRIVATE_KEY_PATH")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT"))
JOB_QUEUE_NAME = os.getenv("JOB_QUEUE_NAME")
REPLY_QUEUE_NAME = "pr_reply_jobs"
CHROMA_HOST = os.getenv("CHROMA_HOST")
CHROMA_PORT = int(os.getenv("CHROMA_PORT"))
CHROMA_COLLECTION_NAME = "codescribe_rules"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"

# --- Setup, Validation, and other functions ---
if not all([APP_ID, PRIVATE_KEY_PATH, OPENAI_API_KEY]): exit(1)
try:
    with open(PRIVATE_KEY_PATH, 'r') as f: PRIVATE_KEY = f.read()
except FileNotFoundError: exit(1)

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
    Your analysis MUST be based *only* on the following internal coding standards.
    These standards OVERRIDE any general knowledge you have.

    **CRITICAL INSTRUCTION:** The user has defined specific rules for variables, constants, and classes. You must follow these rules exactly as written. For example, a module-level assignment like `MyVar = 10` should be treated as a VARIABLE and follow the `snake_case` rule, NOT the constant rule.

    **Internal Standards:**
    {context}

    ---
    
    **Pull Request Details:**
    Review the following pull request. For each violation of the **Internal Standards** provided above, you MUST call the `CodeSuggestion` tool.

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

def analyze_python_file_with_ast(file_content: str):
    logging.info("--- Starting AST Analysis ---")
    try:
        tree = ast.parse(file_content)
        logging.info("Successfully parsed file into an AST.")
        logging.info(f"AST Dump:\n{ast.dump(tree, indent=4)}")
        reconstructed_code = ast.unparse(tree)
        logging.info(f"Reconstructed Code:\n---\n{reconstructed_code}\n---")
    except Exception as e:
        logging.error(f"An error occurred during AST analysis: {e}", exc_info=True)
    logging.info("--- Finished AST Analysis ---")


# --- Handler Functions ---

def handle_pr_review(job_data: dict, review_chain, github_client: GitHub):
    """Handles the entire process of a new PR review."""
    repo_full_name = job_data["repo_full_name"]
    pr_number = job_data["pr_number"]
    owner, repo = repo_full_name.split('/')

    # --- FULL LOGIC IS NOW CORRECTLY PLACED HERE ---
    
    # 1. Perform AST Analysis on any Python files
    files_in_pr_response = github_client.rest.pulls.list_files(owner=owner, repo=repo, pull_number=pr_number)
    pr_details = github_client.rest.pulls.get(owner=owner, repo=repo, pull_number=pr_number).parsed_data
    head_branch_ref = pr_details.head.ref
    for file in files_in_pr_response.parsed_data:
        if file.filename.endswith(".py"):
            logging.info(f"Found Python file: {file.filename}")
            content_response = github_client.rest.repos.get_content(owner=owner, repo=repo, path=file.filename, ref=head_branch_ref)
            base64_content = content_response.parsed_data.content
            decoded_bytes = base64.b64decode(base64_content)
            file_content = decoded_bytes.decode('utf-8')
            analyze_python_file_with_ast(file_content)

    # 2. Perform the RAG-based review
    logging.info("Proceeding with RAG review process...")
    diff_response = github_client.rest.pulls.get(owner=owner, repo=repo, pull_number=pr_number, headers={"Accept": "application/vnd.github.v3.diff"})
    pr_diff = diff_response.content.decode('utf-8')
    pr_title = pr_details.title
    pr_body = pr_details.body or ""
    
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


def handle_comment_reply(job_data: dict, github_client: GitHub):
    """Handles a reply to one of the bot's comments."""
    repo_full_name, pr_number, commenter_login = job_data["repo_full_name"], job_data["pr_number"], job_data["commenter_login"]
    owner, repo = repo_full_name.split('/')
    
    comments_response = github_client.rest.issues.list_comments(owner=owner, repo=repo, issue_number=pr_number)
    conversation_history = [f"User '{c.user.login}' said:\n{c.body}" for c in comments_response.parsed_data]
    conversation_text = "\n\n---\n\n".join(conversation_history)

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.7)
    prompt = f"""
    You are an expert AI code reviewer named CodeScribe. A developer has replied to you.
    Provide a helpful, concise response based on the entire conversation.

    **Full Conversation History:**
    {conversation_text}

    ---
    The last comment was from '{commenter_login}'. Your job is to reply to this. What is your response?
    """

    logging.info("Invoking LLM for a conversational reply...")
    response = llm.invoke(prompt)
    reply_body = response.content

    github_client.rest.issues.create_comment(owner=owner, repo=repo, issue_number=pr_number, body=reply_body)
    logging.info(f"Successfully posted conversational reply to PR #{pr_number}")


# --- Main Worker Logic ---
def process_jobs():
    logging.info("Worker started with Chat support. Listening to multiple queues...")
    review_chain = get_review_chain()
    
    try:
        redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=0)
        redis_client.ping()
        logging.info(f"Successfully connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
    except redis.exceptions.ConnectionError as e: return

    queues = [JOB_QUEUE_NAME, REPLY_QUEUE_NAME]
    while True:
        try:
            queue_name_bytes, job_json = redis_client.brpop(queues, timeout=0)
            queue_name = queue_name_bytes.decode('utf-8')
            job_data = json.loads(job_json)

            github_client = get_installation_client(job_data["installation_id"])
            
            if queue_name == JOB_QUEUE_NAME:
                logging.info(f"Processing PR Review job from '{queue_name}'")
                handle_pr_review(job_data, review_chain, github_client)
            
            elif queue_name == REPLY_QUEUE_NAME:
                if job_data["commenter_login"] != BOT_NAME:
                    logging.info(f"Processing Comment Reply job from '{queue_name}'")
                    handle_comment_reply(job_data, github_client)
                else:
                    logging.info(f"Ignoring comment from our own bot ('{BOT_NAME}') to prevent loops.")

        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}", exc_info=True)
            if 'job_json' in locals() and redis_client and 'queue_name' in locals():
                redis_client.lpush(queue_name, job_json)
            time.sleep(5)

if __name__ == "__main__":
    process_jobs()