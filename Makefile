
.PHONY: dev run install

dev:
	uv run uvicorn main:app --reload

run:
	uv run uvicorn main:app --host 0.0.0.0 --port 8000

install:
	uv sync
