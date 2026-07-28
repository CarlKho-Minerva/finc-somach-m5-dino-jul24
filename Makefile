.PHONY: mock hardware drive drive-hybrid drive-mock drive-classifier probe test

mock:
	./scripts/start-demo.sh --mock

hardware:
	./scripts/start-demo.sh --hardware --prompt-accessibility

drive:
	./scripts/start-drive-dashboard.sh --hardware --prompt-accessibility

drive-hybrid:
	./scripts/start-drive-dashboard.sh --hybrid --prompt-accessibility

drive-mock:
	./scripts/start-drive-dashboard.sh --mock --no-keypress

# Retained as an experimental path; the user-facing drive demo is thresholded.
drive-classifier:
	./scripts/start-drive.sh train-play --channels 2

probe:
	uv run python backend/tools/probe_hardware.py

test:
	./scripts/verify.sh
