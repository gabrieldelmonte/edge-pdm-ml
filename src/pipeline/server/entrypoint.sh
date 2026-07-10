#!/bin/sh
set -e

if [ -d /models-source ]; then
  ln -sfn /models-source /app/models
fi

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
