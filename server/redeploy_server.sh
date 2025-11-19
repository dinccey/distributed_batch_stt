#!/bin/bash

git pull origin main
podman build -t whisper-server .
podman run -d -p 8000:8000 --replace --restart=always --name=whsiper-server -v ./inprogress.txt:/app/inprogress.txt:Z -v ./processed.csv:/app/processed.csv:Z -v /home/shared/video:/mnt/data/video:Z -v ./logs:/app/logs:Z whisper-server