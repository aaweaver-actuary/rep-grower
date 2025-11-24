lint:
	uv run ruff check --fix src/rep_grow
	uv run ruff format src/rep_grow
	uv run ty check src/rep_grow
	cargo fmt
	cargo clippy --all-targets --all-features -- -D warnings

test:
	uv run pytest -vvx src/rep_grow/tests
	cargo test --no-default-features
	cargo test --release --no-default-features

check: lint test