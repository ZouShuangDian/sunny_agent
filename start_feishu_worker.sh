#!/bin/bash
set -e

echo "Starting Feishu Worker..."
echo "Step 1: Installing dependencies..."
pip install -r requirements.txt -q
pip install arq --no-deps -q

echo "Step 2: Running database migrations..."
alembic upgrade heads

echo "Step 3: Starting Feishu arq worker..."
arq app.feishu.worker_feishu.WorkerSettings