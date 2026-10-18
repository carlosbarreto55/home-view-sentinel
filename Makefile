# Every routine command for HomeView Sentinel. Python runs through uv, so
# nothing is installed globally.

UV := uv

.PHONY: setup secrets test lint

setup:
	$(UV) sync

# Asks for the DVR user and password and generates the broker and ntfy
# credentials; everything is stored in the login Keychain.
secrets:
	$(UV) run python -m sentinel.secrets setup

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check src tests
	$(UV) run ruff format --check src tests
