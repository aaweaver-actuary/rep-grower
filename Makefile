lint:
	uv run ruff check --fix src/rep_grow
	uv run ruff format src/rep_grow
	uv run ty check src/rep_grow
	cargo fmt
	cargo clippy --all-targets --all-features -- -D warnings

test:
	uv run pytest --cov=src/rep_grow \
		--cov-fail-under=96 \
		-vvx \
		src/rep_grow
	cargo test --no-default-features
	cargo test --release --no-default-features

check: lint test