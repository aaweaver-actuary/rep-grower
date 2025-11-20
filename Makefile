lint:
	uv run ruff check --fix src/rep_grow
	uv run ruff format src/rep_grow
	uv run ty check src/rep_grow

test:
	uv run pytest -vvx src/rep_grow/tests

check: lint test