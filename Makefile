PY ?= .venv/bin/python

.PHONY: help build validate sensitivity stats test serve clean

help:
	@echo "make build        compute LTS and write data/ artifacts"
	@echo "make validate     golden corridors + aggregate stability checks"
	@echo "make sensitivity  parameter sweep -> docs/sensitivity.md"
	@echo "make stats        print the current build's summary figures"
	@echo "make test         run the test suite"
	@echo "make serve        serve the map locally at http://localhost:8000/"
	@echo "make clean        remove generated artifacts"

build:
	$(PY) -m lexbike build

validate:
	$(PY) -m lexbike validate

sensitivity:
	$(PY) -m lexbike sensitivity

stats:
	$(PY) -m lexbike stats

test:
	$(PY) -m pytest tests/ -v

# The map fetches ./data/... with relative paths, so it must be served from the
# repository root — opening index.html via file:// will fail on CORS.
serve:
	$(PY) -m http.server 8000

clean:
	rm -rf data/ docs/sensitivity.md
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
