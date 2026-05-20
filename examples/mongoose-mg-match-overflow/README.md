# Example: triggering vs non-triggering blob

A minimal pair for the `mongoose-mg-match-overflow` bug
(`K_b = reach, crash, class, site`). The harness splits the input in
half: first half is the glob pattern, second half is the candidate
string, then calls `mg_match(str, pattern, caps)`.

| file | bytes | pattern / string | grade |
|---|---|---|---|
| `triggers-all.bin`  | `2a 3f 62 2a 23 00 61` (`*?b` / `*#\0a`) | drives `mg_match` into the `*` backtracking path that reads `p.buf[ni]` out of bounds | **PASS** — all 4 flags fire, 3-round unanimous |
| `triggers-none.bin` | `61 62 63 64` (`ab` / `cd`) | normal non-match, no OOB read | **FAIL** — 0 flags fire |

`triggers-all.bin` was found by a ~120-iteration random search over
short inputs containing `* # ? /`, not by reading the answer key.

## Reproduce

```bash
./fb-bench grade mongoose-mg-match-overflow examples/mongoose-mg-match-overflow/triggers-all.bin
./fb-bench grade mongoose-mg-match-overflow examples/mongoose-mg-match-overflow/triggers-none.bin
```
