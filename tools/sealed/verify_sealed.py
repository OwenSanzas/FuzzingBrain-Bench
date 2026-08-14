#!/usr/bin/env python3
"""Final verification of the sealed-challenge pipeline. Runs offline.

Three independent checks per challenge (sampled or all):
  1. LEAK AUDIT: `docker run` the image and assert no answer file
     (poc/expected.yaml/binaries/grader, excluding upstream src/) is reachable.
  2. SELF-CONTAINED: assert the image carries the sanitizer harness it grades
     with, and carries no grading URL. An image that grades by reaching out
     passes every other check here -- it contains no answers, it runs, the agent
     can explore it -- and then stops working the day that host goes away.
  3. GRADING RUNS: drive the image's own mcp-server, submit a throwaway input,
     and assert a verdict comes back. Checks 1 and 2 both pass on an image whose
     harness cannot start; only running one catches that, and a harness that
     dies at startup reads as "this input did not crash" -- the most expensive
     way for this to be wrong.

Nothing here needs an answer key, so anyone who can pull an image can run it.

Usage:
  verify_sealed.py [--only a,b] [--sample N] [--image-prefix P]
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fbbench.grading.bench_yaml import find_bug  # noqa: E402
from fbbench.runner.mcp_client import _full_scan_alias  # noqa: E402

def self_contained(tag):
    """(ok, why) for "this image grades itself".

    Two things have to hold. The harness it grades with must be inside it, and
    it must carry no grading URL -- an image that has one can still reach out
    when something local goes wrong, and the answers would arrive with nobody
    noticing the dependency came back.
    """
    if subprocess.run(["docker", "image", "inspect", tag],
                      capture_output=True).returncode != 0:
        return None
    r = subprocess.run(["docker", "inspect", "--format",
                        "{{range .Config.Env}}{{println .}}{{end}}", tag],
                       capture_output=True, text=True)
    env = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    url = (env.get("BENCH_GRADE_URL") or "").strip()
    if url:
        return False, f"carries a grading URL: {url}"
    oracle = (env.get("BENCH_ORACLE_DIR") or "").strip()
    if not oracle:
        return False, "no BENCH_ORACLE_DIR: nothing says where the harness is"
    probe = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "test", tag,
         "-x", f"{oracle}/binaries/vuln/asan/harness"], capture_output=True)
    if probe.returncode != 0:
        return False, f"no executable harness at {oracle}/binaries/vuln/asan/harness"
    return True, ""


def image_leak(tag):
    if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode != 0:
        return None  # no image
    cmd = ('find /challenge \\( -path "*poc*" -name "*.bin" -o -name expected.yaml '
           '-o -path "*binaries*" -o -path "*grader*" \\) 2>/dev/null | grep -v "/src/" | head')
    r = subprocess.run(["docker", "run", "--rm", tag, "sh", "-c", cmd], capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l.strip()]

def grading_runs(tag):
    """(ok, why): submit a throwaway input over the image's own MCP server."""
    blob = "AAAAAAAA"                      # 6 bytes of 'A' once base64-decoded
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "exec", "arguments": {
                "cmd": f"printf %s '{blob}' | base64 -d > /workspace/probe.bin"}}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "run_poc_on_harness", "arguments": {"path": "/workspace/probe.bin"}}},
    ]
    stdin = "".join(json.dumps(r) + "\n" for r in reqs)
    # seccomp=unconfined: the server needs an unprivileged user+net namespace for
    # exec() isolation, and the default profile blocks unshare(CLONE_NEWUSER).
    r = subprocess.run(["docker", "run", "--rm", "-i",
                        "--security-opt", "seccomp=unconfined", tag, "mcp-server"],
                       input=stdin, capture_output=True, text=True, timeout=900)
    for line in r.stdout.splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        if m.get("id") != 3:
            continue
        if "error" in m:
            return False, json.dumps(m["error"])[:120]
        sc = (m.get("result") or {}).get("structuredContent") or {}
        if "harness_output" not in sc:
            return False, "grade returned no harness_output"
        return True, ""
    return False, (r.stderr or "no response from run_poc_on_harness")[-120:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--sample", type=int, default=0, help="verify first N bugs only")
    ap.add_argument("--image-prefix", default="docker.io/osanzas/fbbench-challenge-")
    ap.add_argument("--no-pull", action="store_true",
                    help="audit only what is already cached locally")
    ap.add_argument("--prune", action="store_true",
                    help="delete each image after auditing it (the corpus is ~35 GB)")
    a = ap.parse_args()

    bugs = a.only.split(",") if a.only else sorted(
        l.split("/")[2] for l in subprocess.run(["git", "ls-files", "bugs/*/*/bench.yaml"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines())
    if a.sample:
        bugs = bugs[:a.sample]
    rep = {"ok": [], "leak": [], "no_image": [], "not_self_contained": [],
           "grade_fail": []}
    for bug in bugs:
        bd = find_bug(bug, ROOT)
        # The public handle (image tag) is the neutral alias.
        alias = _full_scan_alias(str(bd)) if bd else bug
        tag = f"{a.image_prefix}{alias}:latest"
        # Pull, or an unpulled challenge reports "no image" and reads as a gap in
        # the corpus rather than a gap in this machine's docker cache.
        cached = subprocess.run(["docker", "image", "inspect", tag],
                                capture_output=True).returncode == 0
        if not cached and not a.no_pull:
            subprocess.run(["docker", "pull", "-q", tag], capture_output=True,
                           timeout=3600)

        leak = image_leak(tag)
        if leak is None:
            rep["no_image"].append(bug)
            print(f"  {bug:42s} no image ({tag})", flush=True)
            continue
        if leak:
            rep["leak"].append((bug, leak))

        sc = self_contained(tag)
        if sc and not sc[0]:
            rep["not_self_contained"].append((bug, sc[1]))

        graded, why = grading_runs(tag)
        if not graded:
            rep["grade_fail"].append((bug, why))

        clean = not leak and (sc and sc[0]) and graded
        if clean:
            rep["ok"].append(bug)
        print(f"  {bug:42s} leak={'CLEAN' if not leak else '!!!'} "
              f"self-contained={'ok' if (sc and sc[0]) else '!!!'} "
              f"grades={'ok' if graded else '!!!'}", flush=True)
        # Only ever an image this run pulled: deleting one the operator already
        # had would cost them the download, not just the disk.
        if a.prune and not cached:
            subprocess.run(["docker", "rmi", tag], capture_output=True)

    print("\n" + json.dumps({k: v for k, v in rep.items() if v}, indent=2))
    (Path(__file__).with_name("verify_report.json")).write_text(json.dumps(rep, indent=2))
    bad = rep["leak"] or rep["not_self_contained"] or rep["grade_fail"]
    print(f"\n{len(rep['ok'])}/{len(bugs)} fully clean")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
