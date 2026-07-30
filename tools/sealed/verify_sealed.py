#!/usr/bin/env python3
"""Final verification of the sealed-challenge pipeline.

Three independent checks per bug (sampled or all):
  1. WIRE: POST the bug's REAL poc to the remote grade server; assert its full
     capability set (bench.yaml K_b) fires -> the oracle bundle + remote wire work.
  2. IMAGE LEAK AUDIT: `docker run` the challenge image and assert no answer file
     (poc/expected.yaml/binaries/grader, excluding upstream src/) is present.
  3. IMAGE ENDPOINT: assert the image's baked-in BENCH_GRADE_URL is not an
     address that only resolves on the machine that built it. An image pointing
     at a docker bridge or at localhost passes every other check here -- it
     contains no answers, it runs, the agent can explore it -- and then refuses
     every grade for everyone who pulls it.

Usage:
  verify_sealed.py --grade-url http://localhost:8077 [--only a,b] [--sample N]
"""
from __future__ import annotations
import argparse, json, subprocess, sys, urllib.request
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fbbench.grading.bench_yaml import find_bug, capability_set  # noqa: E402
from fbbench.runner.mcp_client import _full_scan_alias  # noqa: E402

def remote_grade(url, bug, poc_bytes):
    req = urllib.request.Request(f"{url}/v1/challenges/{bug}/grade", data=poc_bytes,
                                 headers={"Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)

# Addresses that resolve only on the build host. An image carrying one of these
# is unusable by anyone else, and nothing about its CONTENTS says so.
_PRIVATE_HOSTS = ("localhost", "127.0.0.1", "172.17.", "host.docker.internal", "0.0.0.0")


def image_endpoint(bug, prefix="fbbench-challenge"):
    """The image's baked-in grade endpoint, or None if there is no such image.

    Returns ("", ...) when the variable is unset, which is just as broken as a
    private address: grade() has nowhere to go.
    """
    tag = f"{prefix}/{bug}:latest"
    if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode != 0:
        return None
    r = subprocess.run(["docker", "inspect", "--format",
                        "{{range .Config.Env}}{{println .}}{{end}}", tag],
                       capture_output=True, text=True)
    url = next((l.split("=", 1)[1].strip() for l in r.stdout.splitlines()
                if l.startswith("BENCH_GRADE_URL=")), "")
    private = (not url) or any(h in url for h in _PRIVATE_HOSTS)
    return url, private


def image_leak(bug, prefix="fbbench-challenge"):
    tag = f"{prefix}/{bug}:latest"
    if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode != 0:
        return None  # no image
    cmd = ('find /challenge \\( -path "*poc*" -name "*.bin" -o -name expected.yaml '
           '-o -path "*binaries*" -o -path "*grader*" \\) 2>/dev/null | grep -v "/src/" | head')
    r = subprocess.run(["docker", "run", "--rm", tag, "sh", "-c", cmd], capture_output=True, text=True)
    return [l for l in r.stdout.splitlines() if l.strip()]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grade-url", default="http://localhost:8077")
    ap.add_argument("--only", default=None)
    ap.add_argument("--sample", type=int, default=0, help="verify first N bugs only")
    a = ap.parse_args()

    bugs = a.only.split(",") if a.only else sorted(
        l.split("/")[2] for l in subprocess.run(["git", "ls-files", "bugs/*/*/bench.yaml"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines())
    if a.sample:
        bugs = bugs[:a.sample]
    rep = {"wire_ok": [], "wire_fail": [], "leak": [], "no_image": [], "no_poc": [],
           "private_endpoint": []}
    for bug in bugs:
        bd = find_bug(bug, ROOT)
        kb = set(capability_set(bd)) if bd else set()
        # The public handle (image tag + oracle key) is the neutral alias.
        alias = _full_scan_alias(str(bd)) if bd else bug
        # wire — pick the ACTUAL crashing PoC, not a generator/helper. Prefer
        # poc.bin, then any *.bin, never *.py/*.md/*.sh/*.txt.
        pocs = []
        if bd:
            pd = bd / "poc"
            cand = [p for p in sorted(pd.glob("*")) if p.is_file()
                    and p.suffix not in (".py", ".md", ".sh", ".txt", ".yaml")]
            exact = [p for p in cand if p.name == "poc.bin"]
            bins = [p for p in cand if p.suffix == ".bin"]
            pocs = exact or bins or cand
        if not pocs:
            rep["no_poc"].append(bug)
        else:
            try:
                caps = remote_grade(a.grade_url, alias, pocs[0].read_bytes()).get("capabilities", {})
                fired = {k for k, v in caps.items() if v == "fired"}
                if kb.issubset(fired):
                    rep["wire_ok"].append(bug)
                else:
                    rep["wire_fail"].append((bug, sorted(kb - fired)))
            except Exception as e:
                rep["wire_fail"].append((bug, str(e)[:80]))
        # image leak
        leak = image_leak(alias)
        if leak is None:
            rep["no_image"].append(bug)
        elif leak:
            rep["leak"].append((bug, leak))
        # image endpoint — where the image can REACH, as opposed to what it holds
        endpoint = image_endpoint(alias)
        if endpoint is not None and endpoint[1]:
            rep["private_endpoint"].append((bug, endpoint[0]))
        print(f"  {bug:42s} wire={'ok' if bug in rep['wire_ok'] else 'FAIL/na'} "
              f"leak={'CLEAN' if (leak is not None and not leak) else ('!!!' if leak else 'no-img')} "
              f"endpoint={'!!!' if (endpoint and endpoint[1]) else ('ok' if endpoint else 'no-img')}",
              flush=True)
    json.dump(rep, open(ROOT / "tools" / "sealed" / "verify_report.json", "w"), indent=2, default=str)
    print(f"\n=== verify: wire_ok={len(rep['wire_ok'])} wire_fail={len(rep['wire_fail'])} "
          f"image_leak={len(rep['leak'])} private_endpoint={len(rep['private_endpoint'])} "
          f"no_image={len(rep['no_image'])} no_poc={len(rep['no_poc'])} / {len(bugs)} ===")
    if rep["wire_fail"]: print("WIRE FAILURES:", rep["wire_fail"][:5])
    if rep["leak"]: print("*** IMAGE LEAKS:", rep["leak"][:5])
    if rep["private_endpoint"]:
        print("*** PRIVATE ENDPOINTS (these images grade for nobody but their builder):",
              rep["private_endpoint"][:5])

if __name__ == "__main__":
    main()
