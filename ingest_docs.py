import logging
import chromadb # Import the chromadb client library
import os
from dotenv import load_dotenv

from langchain_community.document_loaders import DirectoryLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

# Load environment variables from our local config file
load_dotenv(dotenv_path=".env.local")

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - INGEST - %(levelname)s - %(message)s')

# --- Configuration ---
KNOWLEDGE_BASE_PATH = "./knowledge_base"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
# Read from environment variables, defaulting to localhost for safety
CHROMA_HOST = os.getenv("CHROMA_HOST", "localhost")
CHROMA_PORT = os.getenv("CHROMA_PORT", "8000")
CHROMA_COLLECTION_NAME = "codescribe_rules"

def main():
    """
    Main function to load, split, embed, and store documents in ChromaDB.
    """
    try:
        # 1. Load Documents
        logging.info(f"Loading documents from '{KNOWLEDGE_BASE_PATH}'...")
        loader = DirectoryLoader(KNOWLEDGE_BASE_PATH, glob="**/*.md", show_progress=True)
        documents = loader.load()
        if not documents:
            logging.warning("No documents found in the knowledge base. Exiting.")
            return
        logging.info(f"Loaded {len(documents)} document(s).")

        # 2. Split Documents into Chunks
        logging.info("Splitting documents into chunks...")
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=100)
        chunks = text_splitter.split_documents(documents)
        logging.info(f"Split documents into {len(chunks)} chunks.")

        # 3. Initialize Embedding Model
        logging.info(f"Initializing embedding model '{EMBEDDING_MODEL_NAME}'...")
        embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL_NAME)
        logging.info("Embedding model initialized.")

        # 4. Create ChromaDB client and ingest chunks
        logging.info(f"Connecting to ChromaDB server at {CHROMA_HOST}:{CHROMA_PORT}...")
        
        # CORRECTED STEP: Create a client that connects to the ChromaDB server.
        # This is the modern way to handle client connections.
        chroma_client = chromadb.HttpClient(
            host=CHROMA_HOST,
            port=CHROMA_PORT
        )
        logging.info("ChromaDB client created successfully.")

        logging.info(f"Ingesting chunks into collection '{CHROMA_COLLECTION_NAME}'...")
        
        # Pass the pre-configured client object to LangChain using the 'client' argument.
        vector_store = Chroma.from_documents(
            documents=chunks,
            embedding=embeddings,
            collection_name=CHROMA_COLLECTION_NAME,
            client=chroma_client  # Use the 'client' argument instead of 'client_settings'
        )
        
        logging.info(f"Successfully ingested {len(chunks)} chunks into ChromaDB.")
        logging.info("Vector store is ready for use.")

    except Exception as e:
        logging.error(f"An error occurred during the ingestion process: {e}", exc_info=True)

if __name__ == "__main__":
    main()