.PHONY: install dev demo test lint docker-up docker-down clean

install:
	python -m pip install -e ".[dev]"

dev:
	python -m warbrief serve --reload

demo:
	DEMO_MODE=true LLM_PROVIDER=mock TTS_PROVIDER=mock python -m warbrief demo

test:
	pytest -q

lint:
	ruff check .

docker-up:
	docker compose up --build

docker-down:
	docker compose down

clean:
	rm -rf data/runtime .pytest_cache .ruff_cache
