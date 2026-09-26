# LEVY monorepo convenience targets.
# These wrap tools you already have (uvicorn, bun) and add no new dependencies.

.PHONY: help backend frontend test

help:
	@echo "make backend   # FastAPI gateway on http://localhost:8000 (uvicorn --reload)"
	@echo "make frontend  # UI dev server on http://localhost:5173 (bun run dev)"
	@echo "make test      # backend offline test suite (LEVY_OFFLINE=true pytest)"

backend:
	LEVY_OFFLINE=true uvicorn levy.api.main:app --reload --port 8000

frontend:
	cd web && bun run dev

test:
	LEVY_OFFLINE=true pytest
