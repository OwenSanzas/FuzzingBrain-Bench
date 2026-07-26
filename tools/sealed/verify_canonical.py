#!/usr/bin/env python3
"""End-to-end verification of the CANONICAL path for every sealed challenge.

Unlike verify_sealed.py (which POSTs the PoC straight to the oracle and greps the
image separately), this drives the *actual* runtime: it launches each public
challenge image's own mcp-server over stdio — `docker run -i <image> mcp-server`
— and exercises every tool the agent uses, exactly as an external user would.

Per bug it asserts the four user-required properties:
  1. no errors            — initialize/setup succeed; the container speaks MCP
  2. normal operation     — read_file/list_directory/write_file/exec all work;
                            run_poc_on_harness() fires the full capability set K_b
  3. answers don't leak   — no poc/grader/binaries/expected.yaml reachable (via
                            mcp list/read AND a real in-container `find`); the
                            scrubbed bench.yaml carries no upstream/fix provenance
  4. tool calls don't err — every tool returns a well-formed result, and exec()
                            has NO network (cannot brute-force the remote oracle)

Usage:
  verify_canonical.py [--only a,b] [--sample N] [--workers 3]
                      [--image-prefix docker.io/osanzas/fbbench-challenge-]
"""
from __future__ import annotations
import argparse, json, subprocess, sys, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fbbench.grading.bench_yaml import find_bug, capability_set      # noqa: E402
from fbbench.runner.mcp_client import MCPClient, _full_scan_alias    # noqa: E402

# bench.yaml keys that would point the agent at the upstream report / fix.
SCRUB_KEYS = ("fix_commit", "fix_patch", "vuln_commit", "repo",
              "upstream_report", "cve")
# answer artifacts that must never be reachable inside the container (src/ exempt).
LEAK_FIND = (r'find / -path /proc -prune -o '
             r'\( -name expected.yaml -o -path "*/poc/*" -o -path "*/grader/*" '
             r'-o -path "*/binaries/*" \) -print 2>/dev/null | grep -v "/src/" | head')


def pick_poc(bd: Path) -> bytes | None:
    pd = bd / "poc"
    if not pd.is_dir():
        return None
    cand = [p for p in sorted(pd.glob("*")) if p.is_file()
            and p.suffix not in (".py", ".md", ".sh", ".txt", ".yaml")]
    exact = [p for p in cand if p.name == "poc.bin"]
    bins = [p for p in cand if p.suffix == ".bin"]
    chosen = (exact or bins or cand)
    return chosen[0].read_bytes() if chosen else None


def verify_one(bug: str, image_prefix: str, settle: float = 0.0) -> dict:
    import base64, time
    bd = find_bug(bug, ROOT)
    res = {"bug": bug, "checks": {}, "errors": []}
    if bd is None:
        res["errors"].append("no bug dir"); return res
    alias = _full_scan_alias(str(bd))
    res["alias"] = alias
    image = f"{image_prefix}{alias}"
    kb = set(capability_set(bd))
    poc = pick_poc(bd)

    def ok(name, cond, detail=""):
        res["checks"][name] = bool(cond)
        if not cond and detail:
            res["errors"].append(f"{name}: {detail}")

    m = None
    try:
        m = MCPClient(bug_dir="", workspace="", image=image)
        ok("initialize", bool(m.initialize()))
        s = m.call("setup", {})
        ok("setup_workspace", s.get("workspace_path") == "/workspace", repr(s.get("workspace_path")))
        ok("setup_bugid", s.get("bug_id") == alias, repr(s.get("bug_id")))

        # --- normal operation: list + read ---
        ld = m.call("list_directory", {"path": "/challenge"})
        entries = json.dumps(ld)
        ok("list_directory", "src" in entries or "bench.yaml" in entries, entries[:120])
        # leak via mcp listing: no answer dirs at /challenge top level
        ok("no_answer_dirs", not any(x in entries for x in ('"poc"', '"grader"', '"binaries"')),
           entries[:160])

        by = m.call("read_file", {"path": "/challenge/bench.yaml"})
        bytxt = by.get("content", "") if isinstance(by, dict) else str(by)
        present = [k for k in SCRUB_KEYS if (k + ":") in bytxt]
        ok("bench_scrubbed", not present, f"leaked keys {present}")

        # --- tool calls: write + exec ---
        wf = m.call("write_file", {"path": "/workspace/_probe.txt", "content": "hello"})
        ok("write_file", isinstance(wf, dict) and wf.get("bytes_written") == 5, repr(wf))
        ex = m.call("exec", {"cmd": "echo READY && id -u"})
        ok("exec_runs", isinstance(ex, dict) and ex.get("exit_code") == 0
           and "READY" in (ex.get("stdout") or ""), repr(ex)[:160])
        # exec must have NO network (anti-cheat)
        net = m.call("exec", {"cmd": "timeout 5 bash -c 'echo > /dev/tcp/1.1.1.1/53' 2>&1 "
                                     "&& echo NET_OK || echo NET_BLOCKED"})
        nout = (net.get("stdout", "") + net.get("stderr", "")) if isinstance(net, dict) else str(net)
        ok("exec_no_network", "NET_BLOCKED" in nout and "NET_OK" not in nout, nout[:160])

        # --- leak via a real in-container find (what the agent could actually do) ---
        lk = m.call("exec", {"cmd": LEAK_FIND, "timeout_s": 60})
        lkout = (lk.get("stdout") or "").strip() if isinstance(lk, dict) else str(lk)
        ok("no_answer_files", lkout == "", lkout[:200])

        # --- grade fires K_b through the container -> remote oracle ---
        if poc is None:
            ok("grade_fires", False, "no poc to submit")
        else:
            m.call("write_file", {"path": "/workspace/poc.b64",
                                  "content": base64.b64encode(poc).decode()})
            d = m.call("exec", {"cmd": "base64 -d /workspace/poc.b64 > /workspace/poc.bin"})
            ok("poc_decoded", isinstance(d, dict) and d.get("exit_code") == 0, repr(d)[:120])
            v = m.call("run_poc_on_harness", {"path": "/workspace/poc.bin"})
            caps = v.get("capabilities", {}) if isinstance(v, dict) else {}
            fired = {k for k, vv in caps.items() if vv == "fired"}
            res["fired"] = sorted(fired)
            res["kb"] = sorted(kb)
            ok("grade_fires", kb.issubset(fired), f"missing {sorted(kb - fired)}")
    except Exception as e:
        res["errors"].append("EXC: " + "".join(traceback.format_exception_only(type(e), e)).strip())
    finally:
        if m:
            try: m.close()
            except Exception: pass
    res["pass"] = bool(res["checks"]) and all(res["checks"].values()) and not res["errors"]
    if settle:
        # Let the (small) oracle host release a memory-heavy grade's RSS before
        # the next one — reduces transient differential misses under back-to-back load.
        time.sleep(settle)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--settle", type=float, default=0.0,
                    help="seconds to pause after each bug (eases oracle host memory pressure)")
    ap.add_argument("--image-prefix", default="docker.io/osanzas/fbbench-challenge-")
    ap.add_argument("--out", default=str(ROOT / "tools" / "sealed" / "verify_canonical_report.json"))
    a = ap.parse_args()

    bugs = a.only.split(",") if a.only else sorted(
        l.split("/")[2] for l in subprocess.run(["git", "ls-files", "bugs/*/*/bench.yaml"],
        cwd=ROOT, capture_output=True, text=True).stdout.splitlines())
    if a.sample:
        bugs = bugs[:a.sample]

    results = {}
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(verify_one, b, a.image_prefix, a.settle): b for b in bugs}
        for fut in as_completed(futs):
            r = fut.result()
            results[r["bug"]] = r
            failed = [k for k, v in r["checks"].items() if not v]
            tag = "PASS" if r["pass"] else "FAIL"
            extra = "" if r["pass"] else f"  <- {failed or r['errors'][:1]}"
            print(f"  {tag}  {r['bug']:42s} ({len(r['checks'])} checks){extra}", flush=True)

    passed = [b for b, r in results.items() if r["pass"]]
    failed = [b for b, r in results.items() if not r["pass"]]
    json.dump({"passed": passed, "failed": failed, "results": results},
              open(a.out, "w"), indent=2, default=str)
    print(f"\n=== canonical verify: PASS={len(passed)} FAIL={len(failed)} / {len(bugs)} ===")
    if failed:
        print("FAILURES:")
        for b in failed:
            r = results[b]
            bad = [k for k, v in r["checks"].items() if not v]
            print(f"  {b}: checks_failed={bad} errors={r['errors'][:2]}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
