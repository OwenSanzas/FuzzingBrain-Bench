.PHONY: setup mcp-server deps regression test clean help

PYTHON ?= python3
VENV   ?= .venv

help:
	@echo "FuzzingBrain Bench"
	@echo
	@echo "Targets:"
	@echo "  make setup        Create venv, install the fbbench package, build MCP server"
	@echo "  make mcp-server   Build only the Go MCP server -> bin/mcp-server"
	@echo "  make regression   Grade all shipped PoCs end-to-end; expects all PASS"
	@echo "  make test         Run the pytest suite"
	@echo "  make clean        Remove venv and built binary"
	@echo
	@echo "After 'make setup':"
	@echo "  source $(VENV)/bin/activate"
	@echo "  export ANTHROPIC_API_KEY=..."
	@echo "  python -m fbbench.runner --bug <bug_id> --model claude-opus-4-7"
	@echo "  # or just:  ./fb-bench run <bug_id>"

setup: mcp-server deps
	@echo
	@echo "  ready. activate with:  source $(VENV)/bin/activate"

mcp-server: bin/mcp-server

bin/mcp-server: tools/mcp-server/*.go tools/mcp-server/go.mod
	@command -v go >/dev/null 2>&1 || { echo "go (>=1.22) required"; exit 1; }
	@mkdir -p bin
	CGO_ENABLED=0 go -C tools/mcp-server build -o ../../bin/mcp-server .
	@echo "  built bin/mcp-server ($$(stat -c %s bin/mcp-server) bytes)"

deps: $(VENV)/.deps-installed

$(VENV)/.deps-installed: pyproject.toml
	@command -v $(PYTHON) >/dev/null 2>&1 || { echo "python3 required"; exit 1; }
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip --quiet
	$(VENV)/bin/pip install -e ".[dev]" --quiet
	@touch $@
	@echo "  installed fbbench (editable) into $(VENV)"

regression: bin/mcp-server deps
	@$(VENV)/bin/python -m fbbench.sweep.regression

test: deps
	@$(VENV)/bin/python -m pytest

clean:
	rm -rf $(VENV) bin
