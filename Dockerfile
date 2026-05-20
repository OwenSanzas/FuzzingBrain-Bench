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

# Tier 2 privilege separation. The runner stages an agent sandbox without the
# oracle files (Tier 1), but as defence-in-depth we also make the on-disk
# answer keys + reference PoCs unreadable to the unprivileged agent uid, so a
# stray `find / -name expected.yaml` from exec() cannot read them either. The
# server (root) runs the grader and still reads them fine; exec() drops to
# BENCH_AGENT_UID.
RUN useradd -r -u 10001 -s /usr/sbin/nologin agent \
 && find /work/bugs -depth -type d \( -name grader -o -name poc \) -exec chmod 0700 {} + \
 && find /work/bugs \( -path '*/grader/*' -o -path '*/poc/*' \) -type f -exec chmod 0600 {} +

ENV PYTHONPATH=/work \
    BENCH_AGENT_UID=10001 \
    BENCH_AGENT_GID=10001
ENTRYPOINT ["python", "-m", "runner"]
CMD ["--help"]
