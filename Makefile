.PHONY: install lint format type-check test health clean docker-build

install:
	pip install -r requirements.txt

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .
	ruff check --fix .

type-check:
	mypy config/ agents/ hooks/ mlops/ dataops/ --ignore-missing-imports

test:
	pytest tests/ --cov=. --cov-report=term-missing --cov-fail-under=80

test-fast:
	pytest tests/ -x -q

test-mlops:
	pytest tests/test_mlops_level5.py -v

health:
	python -c "from config.settings import settings; print('Settings OK | project:', settings.gcp_project_id or '(not set)')"
	python -c "import statsmodels; print('statsmodels OK:', statsmodels.__version__)"
	python -c "import anthropic; print('anthropic SDK OK:', anthropic.__version__)"

security:
	bandit -r . -x tests/,docs/ -ll
	pip-audit

docker-build:
	docker build -t spepe:local .

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .mypy_cache .ruff_cache .pytest_cache htmlcov
