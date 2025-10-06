# Dockerfile for the server
# This Dockerfile builds a container for the FastAPI server.
# It uses Python 3.10 as the base image.
# Copy the server.py and any other necessary files into the container.
# Install dependencies.
# Expose port 8000 for uvicorn.
# Mount volumes for audio files and logs when running.

FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Copy server script and requirements
COPY server.py /app/server.py
# If you have other files, copy them here

# Install dependencies
RUN pip install --no-cache-dir fastapi uvicorn

# Expose the port the app runs on
EXPOSE 8000

# Command to run the application
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]

# Example build command (run from the directory containing Dockerfile and server.py):
# docker build -t whisper-server .

# For Podman:
# podman build -t whisper-server .

# Example run command (mount your audio directory and logs):
# docker run -d -p 8000:8000 -v ./logs:/app/logs:Z -v ./inprogress.txt:/app/inprogress.txt:Z -v /mnt/data/video:/mnt/data/video:Z whisper-server

# For Podman:
# podman run -d -p 8000:8000 -v ./logs:/app/logs:Z -v ./inprogress.txt:/app/inprogress.txt:Z -v /mnt/data/video:/mnt/data/video:Z whisper-server
# Note: Replace /mnt/data/video with the actual host path to your MP3 files directory.
# Ensure the container has read/write access to mounted volumes.