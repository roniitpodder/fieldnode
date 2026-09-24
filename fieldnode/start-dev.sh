#!/usr/bin/env bash
# Starts the FastAPI backend and the Vite dev server together.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d backend/venv ]; then
  echo "Creating backend venv..."
  python3 -m venv backend/venv
  backend/venv/bin/pip install -q -r backend/requirements.txt
fi
[ -f backend/.env ] || cp backend/.env.example backend/.env

if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend dependencies..."
  (cd frontend && (command -v pnpm >/dev/null && pnpm install || npm install))
fi

# Seed demo data on first run only.
[ -f backend/fieldnode.db ] || (cd backend && venv/bin/python seed.py)

(cd backend && venv/bin/uvicorn app.main:app --reload --port 8000) &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT

cd frontend && (command -v pnpm >/dev/null && pnpm dev || npm run dev)
