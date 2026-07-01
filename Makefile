.PHONY: test lint typecheck run-chaos evidence report demo clean docker-up docker-down

test:
	pytest -q

lint:
	ruff check src tests scripts

typecheck:
	mypy src

run-chaos:
	python scripts/run_chaos.py --config configs/default.yaml --out reports/metrics.json

evidence:
	python scripts/run_evidence.py

report:
	python scripts/generate_report.py --metrics reports/metrics.json --out reports/auto_report.md

demo:
	python scripts/demo_server.py

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache reports/metrics.json reports/final_report.md
