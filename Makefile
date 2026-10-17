.PHONY: seed test run chaos audit bicep-build

seed:
	PYTHONPATH=src python3 scripts/seed.py

test:
	python3 -m pytest -q

run:
	PYTHONPATH=src uvicorn router.app:app --reload

chaos:
	python3 scripts/chaos_report.py

audit:
	PYTHONPATH=src python3 -m router audit

bicep-build:
	bicep build infra/main.bicep --outfile /tmp/bulkhead-agent-router.json
