# CodeScribe: The AI Pull Request Assistant

CodeScribe is a fully automated, AI-powered agent that integrates with GitHub to supercharge your code review process. When a developer opens a Pull Request, CodeScribe automatically reviews the changes, provides insightful feedback based on your team's custom coding standards, suggests concrete refactors, and engages in a conversation to help developers merge better code, faster.

## ✨ Features

- **🤖 Automated Code Reviews:** Analyzes pull requests for clarity, efficiency, and bugs.
- **💡 Intelligent Suggestions:** Provides one-click code suggestions for identified issues.
- **🧠 RAG-Powered Knowledge:** Bases its reviews on a custom knowledge base, ensuring it adheres to _your_ team's specific standards.
- **💬 Interactive Chat:** Developers can reply to the agent's comments to ask for clarification or request alternative suggestions.
- **🔬 AST-Powered Analysis:** Utilizes Abstract Syntax Trees to understand code structure for more advanced and reliable refactoring capabilities.
- **🚀 Containerized & Deployable:** The entire application is containerized with Docker, allowing for easy setup and deployment.

## 🎬 Demo

_(This is the most important part! Use a screen recorder like Kazam on Linux Mint to record a GIF or short video showing the whole process: you open a PR, the agent comments, you click "Apply suggestion", you reply to the agent, it replies back. Upload this GIF/video to your README.)_

![CodeScribe Demo GIF](path/to/your/demo.gif)

## 🏛️ System Architecture

CodeScribe is a distributed system composed of several microservices that communicate via a Redis job queue.

_(Include the architectural diagram you planned at the beginning. You can create one easily with a tool like diagrams.net (draw.io) and embed the image here.)_

**Key Components:**

- **GitHub App:** The entry point for all webhook events (`pull_request`, `issue_comment`).
- **Ingestion Service (FastAPI):** A secure endpoint that validates and ingests webhooks, placing them onto the appropriate job queue.
- **Redis:** Serves as a robust message broker, decoupling the ingestion service from the processing workers.
- **Worker (Python):** The brains of the system. It picks up jobs, fetches PR data, performs RAG searches against the knowledge base, calls the LLM for analysis, and uses the GitHub API to post comments and suggestions.
- **Vector Database (ChromaDB):** Stores embeddings of the custom coding standards from the `knowledge_base` directory.
- **LLM Provider (OpenAI):** Provides the core intelligence for code analysis and generation.

## 🛠️ Tech Stack

- **Backend:** Python
- **Services:** FastAPI, Redis, ChromaDB
- **AI:** OpenAI API, LangChain (for RAG and Function Calling)
- **Containerization:** Docker & Docker Compose
- **GitHub Integration:** `githubkit`

## 🚀 Getting Started

### Prerequisites

- Docker and Docker Compose
- Python 3.10+
- An active `ngrok` account and token
- An OpenAI API Key
- A GitHub App created with the necessary permissions and events subscribed.

### Setup Instructions

1.  **Clone the Repository:**

    ```bash
    git clone https://github.com/YourUsername/CodeScribe-AI-Agent.git
    cd CodeScribe-AI-Agent
    ```

2.  **Configure Environment Variables:**

    - Create two environment files: `.env.local` (for running the ingestion script) and `.env.docker` (for the containerized services).
    - Use `.env.example` as a template to fill in your GitHub App ID, Webhook Secret, OpenAI API Key, etc.

3.  **Build and Run the System:**

    ```bash
    docker-compose up --build
    ```

4.  **Ingest Knowledge Base:**

    - In a separate terminal, set up a Python virtual environment and install dependencies: `pip install -r requirements.txt`.
    - Run the ingestion script to populate ChromaDB:

    ```bash
    python ingest_docs.py
    ```

5.  **Expose the Webhook:**
    - Run `ngrok http 8001` and update your GitHub App's webhook URL with the provided public address.

The agent is now live and will begin reviewing pull requests on your installed repositories!
