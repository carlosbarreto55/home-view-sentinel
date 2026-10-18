# Every routine command for HomeView Sentinel. Python runs through uv, so
# nothing is installed globally.

UV := uv

.PHONY: setup test lint

setup:
	$(UV) sync

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check src tests
	$(UV) run ruff format --check src tests
