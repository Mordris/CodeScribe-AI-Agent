FROM python:3.10-slim
WORKDIR /app

# The requirements.txt is now at the root of the context
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# We need to copy ONLY the contents of the ingestion_service directory
COPY ./ingestion_service/ .

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]