# Every routine command for HomeView Sentinel. Python runs through uv, so
# nothing is installed globally.

UV    := uv
MODEL ?= yolo11n

.PHONY: setup secrets test lint

# Installs the dependencies and exports the detection model to CoreML. The
# export needs PyTorch, which is removed again afterwards (the final sync).
setup:
	$(UV) sync --group export
	$(UV) run --group export python -m sentinel.detector.export --model $(MODEL)
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
