.PHONY: setup dev-backend dev-frontend test lint ingest eval docker-up

setup:            ## install backend + frontend dependencies
	cd backend && uv sync
	cd frontend && npm install
	@test -f backend/.env || cp backend/.env.example backend/.env

dev-backend:      ## FastAPI on :8000 (auto-reload)
	cd backend && uv run uvicorn app.main:app --reload --port 8000

dev-frontend:     ## Vite dev server on :5173 (proxies /api to :8000)
	cd frontend && npm run dev

test:             ## backend test suite (no network, no API key needed)
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check app tests && uv run ruff format --check app tests
	cd frontend && npm test && npm run build

ingest:           ## refresh the Parquet snapshot from public FAA/BTS sources (+ the UI's home-screen map points)
	cd backend && uv run python -m app.data.ingest && PYTHONPATH=. uv run python scripts/export_map_points.py

eval:             ## run the golden-question evaluation against the configured LLM
	cd backend && uv run python ../evals/run_eval.py

docker-up:        ## build and run everything in one container on :8000
	docker compose up --build
