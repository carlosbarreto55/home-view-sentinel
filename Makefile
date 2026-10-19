# Every routine command for HomeView Sentinel. Python runs through uv, so
# nothing is installed globally.

UV    := uv
MODEL ?= yolo11s

.PHONY: setup secrets install test lint record replay zone-preview

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

# make record CAMERA=garagem MINUTES=10: save a clip from the gateway in clips/.
record:
	$(UV) run python -m sentinel.detector.replay record --camera $(CAMERA) --minutes $(or $(MINUTES),10)

# make replay CLIP=clips/garagem-noite.mp4 CAMERA=garagem: print the events the
# clip would produce; nothing is published.
replay:
	$(UV) run python -m sentinel.detector.replay replay --clip $(CLIP) --camera $(CAMERA)

# make zone-preview CAMERA=garagem: save docs/zone-preview-garagem.png with the
# zones and the current detections drawn on a live frame.
zone-preview:
	$(UV) run python -m sentinel.detector.zone_preview --camera $(CAMERA)

test:
	$(UV) run pytest -q

lint:
	$(UV) run ruff check src tests
	$(UV) run ruff format --check src tests

# Installs the launchd agents (every component starts now and at each login),
# and serves ntfy on the tailnet with Tailscale Serve.
install:
	$(UV) run python -m sentinel.runtime install
