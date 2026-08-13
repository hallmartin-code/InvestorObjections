# Cross-platform venv bin directory: POSIX uses .venv/bin, Windows uses .venv/Scripts.
ifeq ($(OS),Windows_NT)
VENV_BIN := .venv/Scripts
else
VENV_BIN := .venv/bin
endif

CONTEXT ?= initial outreach
PORT ?= 8000

.PHONY: install run serve lint clean

install:
	python -m venv .venv && $(VENV_BIN)/pip install -e ".[dev]"

run:
	$(VENV_BIN)/investor-toolkit $(DECK) --context "$(CONTEXT)"

serve:
	$(VENV_BIN)/uvicorn investor_toolkit.web:app --reload --port $(PORT)

lint:
	$(VENV_BIN)/ruff check investor_toolkit/

clean:
	rm -rf outputs/*.pdf outputs/*.md
