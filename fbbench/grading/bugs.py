"""Which DEFECT each crash points at — the clustering pass over crash signatures.

`signature.py` answers "are these the same crash?" and stops there on purpose: a
signature is fault class plus three frame names, so one defect reached by three
call paths is three signatures. That is the right answer to its question and the
wrong answer to a different one, because the two questions score differently:

    distinct crashes  — how many ways did the model reach a fault?   (a skill)
    distinct bugs     — how many defects did the model actually find? (the score)

A model that reaches one missing bounds check four ways has demonstrated
something real, and it has still found one bug. Both numbers are reported;
neither replaces the other. This module computes the second.

WHAT DECIDES IT. Two crashes are the same defect iff one source change removes
both. Nothing here can read source, so this approximates that test with the
strongest evidence available from stored rows, in order:

  1. the FAULTING FRAME. A defect lives in the code that faults, not in the path
     that got there. libavif-01 signed four times -- avifROStreamRead reached
     from avifParseFileTypeBox, avifROStreamReadBoxHeaderPartial,
     avifROStreamReadU32 and avifROStreamReadU64 -- and all four fault at the
     same memcpy behind the same avifROStreamHasBytesLeft guard. One fix, four
     signatures, one bug.
  2. CLASS IS NOT IDENTITY. Whether an out-of-bounds read is caught as
     heap-buffer-overflow or dies as segv depends on how far past the end it
     landed, not on the bug (libwebp-02 and spirv-tools-01 each signed twice
     with byte-identical frames). So the class is deliberately NOT part of a bug
     key.
  3. RECURSION. Unbounded recursion blows the stack at an arbitrary frame of the
     cycle, so one defect signs once per frame it happened to die on; mutual
     recursion makes the caller order a coin flip, so signatures come out as
     permutations of each other (avro-03).

WHAT IT WILL NOT DO. It never leaves a verdict open: every signature lands in
exactly one cluster, so the reported number is complete and reproducible.
The cost is that it cannot see two things a human reading source can, and both
are recorded per cluster rather than hidden:

  * whether the fault is a defect at all. pdfbox-01's IOExceptions are
    CMapParser's own input validation, reached through a harness declared
    `throws Exception` -- intended behaviour scored as a crash. Clusters carry
    `attributed=False` when there is no frame to attribute them to, but a
    deliberate `throw` looks like any other fault from here.
  * whether two faulting frames share one guard. Different functions stay
    different bugs, which UNDER-merges rather than over-merges: being wrong in
    the direction that credits the model, not the direction that inflates.

Runs off stored rows by design, so a rule change here re-derives every past
run's bug count without re-grading anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from fbbench.grading.signature import SIG_SEP, _STACK_EXHAUSTION, _norm_func

# Frames that allocate or copy on someone else's behalf. When a crash faults
# here the defect belongs to the CALLER -- the unchecked size or length it
# passed in -- so attribution walks one frame down.
#
# signature.py already drops the libc primitives (malloc/calloc/realloc/free/
# operator new) as not-a-frame. What reaches us is the project's own wrappers
# around them, which are real functions in real files and cannot be dropped
# wholesale: `avro_default_allocator` is where every avro allocation faults,
# `g_malloc0`/`g_realloc` where every GLib one does. Matched by name because
# that is the only thing a stored row carries.
_WRAPPER = re.compile(
    r"(?:^|::|_)(?:alloc|malloc|calloc|realloc|free|memdup)(?:_|$)"
    r"|_allocator$|^g_(?:malloc|realloc|free|memdup|slice_)"
    r"|^uprv_(?:malloc|realloc|free)"
    r"|^(?:memcpy|memmove|memset|bcmp|strcpy|strlen)$"
    r"|_ensure_size$|_reserve$",
    re.IGNORECASE)


def split_sig(sig: str) -> list[str]:
    """Undo SIG_SEP.join(_escape(p) ...); "|" is legal inside a C++ name."""
    parts, cur, esc = [], [], False
    for ch in sig:
        if esc:
            cur.append(ch); esc = False
        elif ch == "\\":
            esc = True
        elif ch == SIG_SEP:
            parts.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def parts_of(sig: str) -> tuple[str, list[str]]:
    """(fault class, frame names top-first) for one signature."""
    p = split_sig(sig)
    return p[0], [f for f in p[1:] if f not in ("<no-frames>", "<dos>")]


def attribution_frame(frames: list[str]) -> str | None:
    """The frame that OWNS the defect: the first one that is not a wrapper.

    Falls back to the faulting frame when every frame looks like a wrapper --
    an unchecked size passed between two allocators is still attributable to
    the outer one, and returning None there would throw away a real find.
    """
    for f in frames:
        if not _WRAPPER.search(f):
            return f
    return frames[0] if frames else None


@dataclass
class Bug:
    """One defect, and the crash signatures that turned out to point at it."""

    key: str                                  # what identifies this defect
    signatures: list[str] = field(default_factory=list)
    rule: str = ""                            # which rule merged them
    attributed: bool = True                   # False = no frame to attribute to
    files: list[str] = field(default_factory=list)   # when frame detail is known

    @property
    def paths(self) -> int:
        """How many distinct crash paths reached this one bug."""
        return len(self.signatures)


def _recursion_key(klass: str, frames: list[str]) -> str | None:
    """Identity for a blown stack, which faults at an arbitrary frame.

    The cycle itself is stable even though the frame it dies on is not, so the
    key is the SET of frames, sorted. Two signatures that are permutations of
    one another -- mutual recursion, avro-03 -- collapse here too, which is the
    same phenomenon one level down.
    """
    if klass not in _STACK_EXHAUSTION and not klass.endswith("stackoverflowerror"):
        return None
    return "cycle:" + "/".join(sorted(set(frames)))


def cluster(signatures: dict[str, list[dict]]) -> list[Bug]:
    """Group crash signatures into the distinct defects they point at.

    `signatures` maps canon_sig -> the frames behind it, as score.json's
    `crash_frames` records them: {"func", "file", "line"} each, top frame first.
    A run that recorded no frames -- one graded before they were kept, or a
    truncated trace -- passes an empty list, and every rule that needs only the
    names inside the signature still applies. So the input is always the same
    shape and there is always a verdict; frames just make it sharper, because the
    faulting FILE merges a defect whose faulting frame moves within its own file
    (systemd-01 faults in four trie_* functions of one sd-hwdb.c).

    Determinism: clusters come back ordered, and the signatures inside each are
    sorted, so two runs over the same rows produce the same report.
    """
    frames_of = {s: list(v or []) for s, v in signatures.items()}
    sigs = list(signatures)

    # Signatures that are PERMUTATIONS of each other — same class, same frames,
    # different order — are one defect reached through mutual recursion, where
    # which function is on top depends only on recursion-depth parity
    # (avro-03: `read_value < read_map_value` and `read_map_value < read_value`,
    # both faulting at allocation.c:36). Observing both orders is the evidence
    # that the order carries no information, so those signatures get attributed
    # from their SORTED frames and therefore land together. Detected here rather
    # than merged later because it needs no frame detail: it is visible in the
    # signature strings every row already has.
    perm: dict[tuple[str, frozenset], int] = {}
    for sig in set(sigs):
        klass, names = parts_of(sig)
        if names:
            perm[(klass, frozenset(names))] = perm.get((klass, frozenset(names)), 0) + 1

    buckets: dict[str, Bug] = {}
    for sig in sorted(set(sigs)):
        klass, names = parts_of(sig)
        detail = frames_of.get(sig) or []
        # Prefer the recorded frames (they carry file and line); fall back to
        # the names inside the signature, which every row has.
        # Normalized with signature.py's own rule, not used raw. A recorded frame
        # carries the full demangled name -- parameter lists, template arguments,
        # Rust instantiation hashes -- and two toolchains render the same name
        # differently (`> >` vs `>>`), so a raw name is not a stable key. This is
        # also what makes a frames-based key comparable with a names-based one:
        # both sides then hold the string canon_sig would have used.
        names_detail = [_norm_func(f["func"]) for f in detail if f.get("func")] or names
        if names and perm.get((klass, frozenset(names)), 0) > 1:
            names_detail = sorted(names_detail)

        if not names_detail:
            # Nothing to attribute: `segv|<no-frames>`, `timeout|<dos>`. Kept as
            # its own bug rather than merged into a real one -- it may be a real
            # find, and guessing which would corrupt a neighbouring count.
            key, rule, attributed = f"unattributed:{klass}", "no-frames", False
        elif (rk := _recursion_key(klass, names_detail)):
            key, rule, attributed = rk, "recursion-cycle", True
        else:
            frame = attribution_frame(names_detail)
            key, rule, attributed = f"site:{frame}", "faulting-frame", True

        bug = buckets.get(key)
        if bug is None:
            bug = buckets[key] = Bug(key=key, rule=rule, attributed=attributed)
        bug.signatures.append(sig)
        for f in detail[:1]:
            if f.get("file") and str(f["file"]) not in bug.files:
                bug.files.append(str(f["file"]))

    bugs = list(buckets.values())

    # Second pass, only possible with frame detail: merge bugs whose faulting
    # frames live in the SAME FILE. One defect can fault in several functions of
    # its own file -- systemd-01's trie_fnmatch_f / trie_search_f /
    # trie_children_cmp_f / hwdb_add_property are one corrupt-trie walk in
    # sd-hwdb.c. Skipped without frame detail, since a name cannot say what file
    # it lives in.
    if frames_of:
        by_file: dict[str, list[Bug]] = {}
        loose: list[Bug] = []
        for b in bugs:
            if len(b.files) == 1 and b.attributed:
                by_file.setdefault(b.files[0], []).append(b)
            else:
                loose.append(b)
        merged: list[Bug] = []
        for path, group in by_file.items():
            if len(group) == 1:
                merged.append(group[0])
                continue
            head = Bug(key=f"file:{path}", rule="same-file", attributed=True,
                       files=[path])
            for b in sorted(group, key=lambda b: b.key):
                head.signatures.extend(b.signatures)
            merged.append(head)
        bugs = merged + loose

    for b in bugs:
        b.signatures = sorted(set(b.signatures))
    return sorted(bugs, key=lambda b: (-b.paths, b.key))


def count(signatures: dict[str, list[dict]]) -> int:
    """How many distinct defects these crash signatures point at."""
    return len(cluster(signatures))


def summarize(signatures: dict[str, list[dict]]) -> dict:
    """Report-ready: the two numbers plus the evidence behind the second."""
    bugs = cluster(signatures)
    return {
        "crashes": len(signatures),
        "bugs": len(bugs),
        "unattributed": sum(1 for b in bugs if not b.attributed),
        "clusters": [
            {"key": b.key, "rule": b.rule, "paths": b.paths,
             "attributed": b.attributed, "signatures": b.signatures}
            for b in bugs],
    }
