# Dockerfile for the whisper-server (FastAPI + SQLite version)
FROM python:3.13-slim

# Set working directory
WORKDIR /app

# First copy ONLY what's needed for installing dependencies
# (this allows Docker to cache the pip install layer unless requirements change)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the application code
COPY server.py .

# Create directories that will be used for logs/tasks (even if mounted later)
RUN mkdir -p /app/logs

# Expose port
EXPOSE 8000

# Run the server
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]