# Runner environment for FuzzingBrain Bench.
#
# Contains: Python + anthropic SDK + the built MCP server binary.
# Use this when evaluating models on a fresh machine that doesn't have
# Go installed locally.
#
#   docker build -t fbbench-runner .
#   docker run --rm -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
#       -v $(pwd)/runs:/work/runs fbbench-runner \
#       --bug netsnmp-vacm-parse-npd --model claude-opus-4-7 --seed 0

FROM golang:1.22-bookworm AS mcp-build
WORKDIR /src
COPY tools/mcp-server/ tools/mcp-server/
RUN CGO_ENABLED=0 go -C tools/mcp-server build -o /out/mcp-server .

FROM python:3.12-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /work

# Python deps
COPY runner/requirements.txt /work/runner/requirements.txt
RUN pip install --no-cache-dir -r runner/requirements.txt

# MCP server binary
COPY --from=mcp-build /out/mcp-server /work/bin/mcp-server

# Everything else the runner needs: bugs/, runner/, scripts/
COPY runner/  /work/runner/
COPY scripts/ /work/scripts/
COPY bugs/    /work/bugs/
COPY docs/SPEC.md docs/bench-corpus.json /work/docs/

ENV PYTHONPATH=/work
ENTRYPOINT ["python", "-m", "runner"]
CMD ["--help"]
