FROM python:3.10-slim
WORKDIR /app

# The requirements.txt is now at the root of the context
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# We need to copy ONLY the contents of the worker directory
COPY ./worker/ .

# This path is now correct, relative to the project root context
COPY ./knowledge_base ./knowledge_base

CMD ["python", "main.py"]