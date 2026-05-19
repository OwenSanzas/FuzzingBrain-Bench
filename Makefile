.PHONY: setup mcp-server runner-deps regression clean help

PYTHON ?= python3
VENV   ?= .venv

help:
	@echo "FuzzingBrain Bench"
	@echo
	@echo "Targets:"
	@echo "  make setup        Create venv, install runner deps, build MCP server"
	@echo "  make mcp-server   Build only the Go MCP server -> bin/mcp-server"
	@echo "  make regression   Grade all 16 PoCs end-to-end; expects 16/16 PASS"
	@echo "  make clean        Remove venv and built binary"
	@echo
	@echo "After 'make setup':"
	@echo "  source $(VENV)/bin/activate"
	@echo "  export ANTHROPIC_API_KEY=..."
	@echo "  python -m runner --bug <bug_id> --model claude-opus-4-7 --seed 0"

setup: mcp-server runner-deps
	@echo
	@echo "  ready. activate with:  source $(VENV)/bin/activate"

mcp-server: bin/mcp-server

bin/mcp-server: tools/mcp-server/*.go tools/mcp-server/go.mod
	@command -v go >/dev/null 2>&1 || { echo "go (>=1.22) required"; exit 1; }
	@mkdir -p bin
	CGO_ENABLED=0 go -C tools/mcp-server build -o ../../bin/mcp-server .
	@echo "  built bin/mcp-server ($$(stat -c %s bin/mcp-server) bytes)"

runner-deps: $(VENV)/.deps-installed

$(VENV)/.deps-installed: runner/requirements.txt
	@command -v $(PYTHON) >/dev/null 2>&1 || { echo "python3 required"; exit 1; }
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip --quiet
	$(VENV)/bin/pip install -r runner/requirements.txt --quiet
	@touch $@
	@echo "  installed runner deps into $(VENV)"

regression: bin/mcp-server runner-deps
	@$(VENV)/bin/python scripts/regression.py

clean:
	rm -rf $(VENV) bin
