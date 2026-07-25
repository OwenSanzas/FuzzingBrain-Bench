"""Live full-screen dashboard for the FuzzingBrain Bench sweep.

Modelled on the AGF pipeline dashboard: a process-global :data:`STATUS`
singleton holds a thread-safe snapshot of every (model x bug x sample) cell,
and :func:`dashboard` renders a full-screen Rich ``Live`` view — a pinned
header (banner + overall progress + running cost + elapsed), a per-model
aggregate panel, the cell that is actively running (with live turn / current
tool / grade attempts), a tail of recently finished cells, and a scrolling
event log.

The sweep orchestrator runs each episode as a subprocess that flushes
``episode.jsonl`` line by line; :func:`run_cell_tailing` follows that file
while the child runs and feeds turn-level events into ``STATUS`` so the
dashboard stays live without the orchestrator and the agent sharing memory.

All mutators are lock-guarded: the Rich ``Live`` refresh runs on its own
thread while the orchestrator updates cell state from the main thread.
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# The capability ladder, strongest-last, with the single-letter column heads
# used in the compact ladder cell. Mirrors fbbench.cli.console.TIERS.
LADDER = ["reach", "crash", "differential", "class", "site"]
LADDER_HEADS = {"reach": "R", "crash": "C", "differential": "D", "class": "K", "site": "S"}

# phase -> (glyph, rich style). Ordered roughly by lifecycle progression.
_PHASE_STYLE: dict[str, tuple[str, str]] = {
    "pending": ("·", "dim"),
    "running": ("▶", "bold magenta"),
    "done": ("✓", "bold green"),
    "error": ("✗", "bold red"),
    "skipped": ("⏭", "dim"),
}


def _fmt_elapsed(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


@dataclass
class Cell:
    """Live state for one (model, bug, sample) sweep cell."""
    model: str
    bug: str
    sample: int
    phase: str = "pending"          # pending | running | done | error | skipped
    turn: int = 0                   # current/last turn (1-based)
    max_turns: int = 0
    tool: str = ""                  # tool of the most recent tool_result
    grades: int = 0                 # number of grade() calls so far
    kb: list[str] = field(default_factory=list)   # capability_set (applicable flags)
    caps: dict[str, str] = field(default_factory=dict)  # flag -> fired/not_fired/n-a
    solved_val: bool | None = None  # authoritative solve (score.solved); None = unknown
    tier: int = 0
    cost: float = 0.0
    reason: str = ""                # terminated_reason
    error: str = ""
    t_start: float = 0.0
    t_end: float = 0.0

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.model, self.bug, self.sample)

    @property
    def solved(self) -> bool:
        # Authoritative: a single candidate reproduced the full target defect
        # (score.solved). Fall back to the caps for older runs that predate the
        # field. NEVER a sticky union across candidates.
        if self.solved_val is not None:
            return self.solved_val
        ks = self.kb or LADDER
        return bool(self.caps) and all(self.caps.get(k) == "fired" for k in ks)


class SweepStatus:
    """Process-global, thread-safe snapshot of the running sweep."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.exp = ""
        self.models: list[str] = []
        self.bugs: list[str] = []
        self.samples: list[int] = []
        self.max_turns = 0
        self.total = 0
        self.already_done = 0
        self.t0 = 0.0
        self.total_cost = 0.0
        self._cells: dict[tuple[str, str, int], Cell] = {}
        self._order: list[tuple[str, str, int]] = []
        self._recent: deque[tuple[str, str, int]] = deque(maxlen=40)
        self._log: deque[Text] = deque(maxlen=500)

    # ---- configuration --------------------------------------------------
    def configure(self, *, exp: str, models: list[str], bugs: list[str],
                  samples: list[int], max_turns: int,
                  total: int, already_done: int) -> None:
        with self._lock:
            self.exp = exp
            self.models = models
            self.bugs = bugs
            self.samples = samples
            self.max_turns = max_turns
            self.total = total
            self.already_done = already_done
            self.t0 = time.time()

    # ---- mutators -------------------------------------------------------
    def _cell(self, model: str, bug: str, sample: int) -> Cell:
        key = (model, bug, sample)
        c = self._cells.get(key)
        if c is None:
            c = Cell(model=model, bug=bug, sample=sample, max_turns=self.max_turns)
            self._cells[key] = c
            self._order.append(key)
        return c

    def cell_start(self, model: str, bug: str, sample: int, kb: list[str]) -> None:
        with self._lock:
            c = self._cell(model, bug, sample)
            c.phase = "running"
            c.kb = kb
            c.t_start = time.time()
            self.log(f"▶ {bug} · {model} · sample-{sample}", "magenta")

    def feed_event(self, model: str, bug: str, sample: int, ev: dict) -> None:
        """Apply one parsed episode.jsonl record to the cell."""
        with self._lock:
            c = self._cell(model, bug, sample)
            kind = ev.get("event")
            if kind == "assistant":
                c.turn = int(ev.get("turn", c.turn - 1)) + 1
            elif kind == "tool_result":
                c.tool = ev.get("tool", c.tool)
                if c.tool == "grade":
                    c.grades += 1
            elif kind == "end":
                c.caps = ev.get("capabilities", c.caps)
                if "solved" in ev:
                    c.solved_val = bool(ev["solved"])
                c.reason = ev.get("terminated_reason", c.reason)
                c.turn = int(ev.get("turns_used", c.turn))

    def cell_finish(self, model: str, bug: str, sample: int, score: dict | None) -> None:
        with self._lock:
            c = self._cell(model, bug, sample)
            c.t_end = time.time()
            if score and "error" not in score:
                c.caps = score.get("capabilities", c.caps)
                if "solved" in score:
                    c.solved_val = bool(score["solved"])
                c.tier = int(score.get("tier_score", 0))
                c.cost = float(score.get("total_usd") or 0.0)
                c.reason = score.get("terminated_reason", c.reason)
                c.phase = "error" if c.reason == "error" else "done"
                if c.reason == "error":
                    c.error = score.get("error", "")
                self.total_cost += c.cost
            else:
                c.phase = "error"
                c.error = (score or {}).get("error", "unknown")
            self._recent.append(c.key)
            glyph = "✓" if c.phase == "done" else "✗"
            style = "green" if c.solved else ("red" if c.phase == "error" else "yellow")
            tail = c.error if c.phase == "error" else f"tier {c.tier}/5 · {c.reason}"
            self.log(f"{glyph} {bug} · {model} · {tail} · ${c.cost:.4f}", style)

    def cell_skip(self, model: str, bug: str, sample: int) -> None:
        with self._lock:
            c = self._cell(model, bug, sample)
            c.phase = "skipped"

    def log(self, msg: str, style: str = "") -> None:
        with self._lock:
            stamp = _fmt_elapsed(time.time() - self.t0) if self.t0 else "0s"
            line = Text()
            line.append(f"{stamp:>8} ", style="dim")
            line.append(msg, style=style)
            self._log.append(line)

    # ---- derived counts -------------------------------------------------
    def _counts(self) -> tuple[int, int, int]:
        done = sum(1 for c in self._cells.values() if c.phase in ("done", "error"))
        running = sum(1 for c in self._cells.values() if c.phase == "running")
        return self.already_done + done, running, done

    # ---- panels ---------------------------------------------------------
    def header_panel(self) -> Panel:
        with self._lock:
            done, running, _ = self._counts()
            elapsed = _fmt_elapsed(time.time() - self.t0) if self.t0 else "0s"
            head = Text()
            head.append("FB·BENCH", style="bold green")
            head.append("  sweep  ")
            head.append(f"{done}/{self.total}", style="bold")
            head.append(" cells · ")
            head.append(f"{len(self.models)}m×{len(self.bugs)}b×{len(self.samples)}s")
            head.append("  ·  ")
            head.append(f"{running} active", style="magenta" if running else "dim")
            head.append("  ·  ")
            head.append(f"${self.total_cost:.2f}", style="bold yellow")
            head.append("  ·  ")
            head.append(elapsed, style="cyan")
            sub = Text(no_wrap=True)
            sub.append(f"out {self.exp}", style="dim")
            sub.append(f"  ·  max_turns {self.max_turns}", style="dim")
            if self.already_done:
                sub.append(f"  ·  {self.already_done} resumed", style="dim")
            return Panel(Group(head, sub), border_style="green", padding=(0, 1))

    def _ladder_text(self, c: Cell) -> Text:
        # fired -> the bold-green capital (R C D K S); everything else -> a dim
        # dot, so the lit rungs read at a glance even without colour.
        t = Text(no_wrap=True)
        for k in LADDER:
            head = LADDER_HEADS[k]
            applicable = (not c.kb) or (k in c.kb)
            state = c.caps.get(k)
            if not c.caps:
                t.append(head, style="dim")           # not graded yet
            elif applicable and state == "fired":
                t.append(head, style="bold green")
            elif not applicable or state == "n/a":
                t.append("·", style="dim")            # not in this bug's set
            else:
                t.append("·", style="red")            # applicable but not fired
            t.append(" ")
        return t

    def models_panel(self) -> Panel:
        with self._lock:
            t = Table.grid(padding=(0, 2))
            t.add_column("model", style="bold")
            t.add_column("done", justify="right")
            t.add_column("solved", justify="right")
            for k in LADDER:
                t.add_column(LADDER_HEADS[k], justify="right")
            t.add_column("$", justify="right")
            t.add_row("model", "done", "solved",
                      *[Text(LADDER_HEADS[k], style="dim") for k in LADDER],
                      "cost", style="dim")
            for model in self.models:
                cells = [c for c in self._cells.values() if c.model == model]
                graded = [c for c in cells if c.caps]
                n_cells = len(self.bugs) * len(self.samples)
                done = sum(1 for c in cells if c.phase in ("done", "error", "skipped"))
                solved = sum(1 for c in graded if c.solved)
                agg = {k: sum(1 for c in graded if c.caps.get(k) == "fired") for k in LADDER}
                cost = sum(c.cost for c in cells)
                t.add_row(
                    model,
                    f"{done}/{n_cells}",
                    Text(str(solved), style="green" if solved else "dim"),
                    *[Text(str(agg[k]), style="green" if agg[k] else "dim") for k in LADDER],
                    f"${cost:.2f}",
                )
            return Panel(t, title="models", title_align="left",
                         border_style="blue", padding=(0, 1))

    def active_panel(self) -> Panel:
        with self._lock:
            running = [self._cells[k] for k in self._order
                       if self._cells[k].phase == "running"]
            if not running:
                return Panel(Text("idle — no cell running", style="dim italic"),
                             title="active", title_align="left",
                             border_style="magenta", padding=(0, 1))
            t = Table.grid(padding=(0, 2))
            for _ in range(6):
                t.add_column()
            for c in running:
                bar = self._turn_bar(c)
                t.add_row(
                    Text("▶", style="bold magenta"),
                    Text(f"{c.bug}", style="bold"),
                    Text(c.model, style="cyan"),
                    bar,
                    Text(f"{c.tool or '…'}", style="yellow"),
                    Text(f"graded ×{c.grades}", style="dim"),
                )
            return Panel(t, title="active", title_align="left",
                         border_style="magenta", padding=(0, 1))

    def _turn_bar(self, c: Cell) -> Text:
        width = 16
        frac = (c.turn / c.max_turns) if c.max_turns else 0.0
        frac = max(0.0, min(1.0, frac))
        filled = int(frac * width)
        t = Text(no_wrap=True)
        t.append("turn ", style="dim")
        t.append("█" * filled, style="magenta")
        t.append("░" * (width - filled), style="dim")
        t.append(f" {c.turn}/{c.max_turns}", style="dim")
        return t

    def recent_panel(self, max_rows: int = 12) -> Panel:
        with self._lock:
            keys = [k for k in reversed(self._recent)][:max_rows]
            t = Table.grid(padding=(0, 2))
            t.add_column()  # glyph
            t.add_column(style="bold")  # bug
            t.add_column(style="cyan")  # model
            t.add_column()  # ladder
            t.add_column(justify="right")  # tier
            t.add_column(justify="right")  # cost
            t.add_column(style="dim")  # reason
            for key in keys:
                c = self._cells[key]
                glyph, style = _PHASE_STYLE.get(c.phase, ("·", "dim"))
                t.add_row(
                    Text(glyph, style=style),
                    c.bug,
                    c.model,
                    self._ladder_text(c),
                    Text(f"{c.tier}/5", style="green" if c.solved else "dim"),
                    Text(f"${c.cost:.4f}", style="dim"),
                    Text(c.error or c.reason, style="dim", no_wrap=True),
                )
            if not keys:
                t.add_row(Text("(none finished yet)", style="dim italic"))
            return Panel(t, title="recent", title_align="left",
                         border_style="blue", padding=(0, 1))

    def log_panel(self, max_rows: int = 8) -> Panel:
        with self._lock:
            lines = list(self._log)[-max_rows:]
            body = Group(*lines) if lines else Text("(no events yet)", style="dim italic")
            return Panel(body, title="log", title_align="left",
                         border_style="dim", padding=(0, 1))

    # ---- composite ------------------------------------------------------
    def render(self) -> Group:
        return Group(
            self.header_panel(),
            self.models_panel(),
            self.active_panel(),
            self.recent_panel(),
            self.log_panel(),
        )


STATUS = SweepStatus()


class _Dashboard:
    """Rich renderable that re-reads STATUS on every refresh."""

    def __rich__(self) -> Group:
        return STATUS.render()


@contextmanager
def dashboard(console: Console | None, *, enabled: bool = True) -> Iterator[None]:
    """Full-screen live sweep dashboard. No-op on non-TTY output.

    On exit the alternate screen is torn down, so a static snapshot of the
    final state is re-printed onto the normal buffer to persist the summary.
    """
    from rich.live import Live
    if not enabled or console is None or not console.is_terminal:
        yield
        return
    try:
        with Live(_Dashboard(), console=console, screen=True,
                  refresh_per_second=4, transient=False):
            yield
    finally:
        try:
            console.print(STATUS.header_panel())
            console.print(STATUS.models_panel())
            console.print(STATUS.recent_panel(max_rows=20))
        except Exception:  # noqa: BLE001
            pass


def run_cell_tailing(cmd: list[str], cwd: str, timeout: int, episode_path: Path,
                     model: str, bug: str, sample: int) -> dict | None:
    """Run one episode subprocess, tailing its episode.jsonl into STATUS.

    Returns the parsed score.json (or an ``{"error": ...}`` dict), matching
    the contract of the orchestrator's plain ``run_cell``.
    """
    # Start fresh: drop any stale ledger from a previous partial run.
    try:
        episode_path.unlink()
    except FileNotFoundError:
        pass

    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    deadline = time.time() + timeout
    pos = 0
    buf = ""
    killed = False
    while True:
        # Drain any new episode.jsonl lines into STATUS.
        if episode_path.is_file():
            try:
                with episode_path.open("r") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                buf += chunk
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        STATUS.feed_event(model, bug, sample, json.loads(line))
                    except ValueError:
                        pass
            except OSError:
                pass
        if proc.poll() is not None:
            break
        if time.time() > deadline:
            proc.kill()
            killed = True
            break
        time.sleep(0.2)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    if killed:
        return {"error": "timeout"}
    sj = episode_path.with_name("score.json")
    return json.loads(sj.read_text()) if sj.is_file() else {"error": "no score.json"}


def _preview(static: bool = False) -> None:
    """Drive STATUS with fake cells so the dashboard layout can be eyeballed.

        python -m fbbench.sweep.dashboard            # ~animated live demo
        python -m fbbench.sweep.dashboard --static   # one final snapshot
    """
    from rich.console import Console
    models = ["claude-opus-4-8", "gemini-2.5-pro", "claude-haiku-4-5"]
    bugs = ["avro-03", "freerdp-01", "openssl-02", "mongoose-01", "skia-01"]
    kb = LADDER
    console = Console()
    STATUS.configure(exp="exp-preview", models=models, bugs=bugs, samples=[0],
                     max_turns=30, total=len(models) * len(bugs),
                     already_done=0)

    # Deterministic pseudo-outcomes (no Math.random equivalent needed).
    def outcome(i: int) -> dict:
        fired = (i * 7) % 6           # 0..5 flags fired, varied per cell
        caps = {k: ("fired" if j < fired else "not_fired") for j, k in enumerate(LADDER)}
        tier = sum(1 for v in caps.values() if v == "fired")
        reason = "site" if tier == 5 else ("error" if i % 11 == 0 else "max_turns")
        if reason == "error":
            return {"error": "AuthenticationError: 401"}
        return {"capabilities": caps, "tier_score": tier, "terminated_reason": reason,
                "total_usd": 0.05 + (i % 5) * 0.04}

    cells = [(m, b) for m in models for b in bugs]
    if static:
        for i, (m, b) in enumerate(cells):
            STATUS.cell_start(m, b, 0, kb)
            STATUS.cell_finish(m, b, 0, outcome(i))
        console.print(STATUS.render())
        return

    with dashboard(console, enabled=True):
        for i, (m, b) in enumerate(cells):
            STATUS.cell_start(m, b, 0, kb)
            for turn in range(0, 30, 3):
                STATUS.feed_event(m, b, 0, {"event": "assistant", "turn": turn})
                tool = ["list_directory", "read_file", "exec", "write_file", "grade"][turn % 5]
                STATUS.feed_event(m, b, 0, {"event": "tool_result", "turn": turn, "tool": tool})
                time.sleep(0.05)
            STATUS.cell_finish(m, b, 0, outcome(i))
            time.sleep(0.15)
        time.sleep(1.0)


if __name__ == "__main__":
    import sys
    _preview(static="--static" in sys.argv)
