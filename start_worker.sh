#!/bin/bash
set -e

echo "Starting worker..."
echo "Step 1: Installing dependencies..."
pip install -r requirements.txt -q
pip install arq --no-deps -q

echo "Step 2: Running database migrations..."
alembic upgrade heads

echo "Step 3: Starting arq worker..."
arq app.worker.WorkerSettings
