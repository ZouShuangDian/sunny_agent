#!/bin/bash
set -e

echo "Starting application..."
echo "Step 1: Running database migrations..."
alembic upgrade head

echo "Step 2: Starting main application..."
python app/main.py
