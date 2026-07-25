.PHONY: mock hardware probe test

mock:
	./scripts/start-demo.sh --mock

hardware:
	./scripts/start-demo.sh --hardware --prompt-accessibility

probe:
	uv run python backend/tools/probe_hardware.py

test:
	./scripts/verify.sh
