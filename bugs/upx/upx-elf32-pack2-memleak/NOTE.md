# upx-elf32-pack2-memleak — partial build infra, deferred

Build infra works (Dockerfile reuses the upx-elf64-generate-overflow
recipe: `objcopy --redefine-sym main=upx_orig_main` to merge UPX
main.cpp.o into the libFuzzer harness).

Status:
- Image `fb-bench-upx-elf32` builds cleanly at the same vuln_commit
  `1ebd3356f36780960a03354a8ded23410ebc7e79` ("Cleanup large rebase")
  used by upx-elf64-generate-overflow.
- 5-minute corpus fuzz over a small seed corpus (8 KiB synthesised
  ELF32 + random mutations) finds zero memleaks under LSan.
- bench-corpus.json lists `vuln_commit: null` for this bug; maintainer
  closed upstream issue #945 with "Done" but no fix commit referenced
  in the issue thread (review notes: "may be unfixed-but-marked-fixed,
  or fixed silently inside a large rebase").

The "Cleanup large rebase" commit may itself contain the fix, or the
fix may have landed in a later riscv64 commit (#bd09247, c40311a2,
etc.) that incidentally rewrote ElfLinker::addLoader. Bisecting the
fix commit needs the original PoC from upstream issue #945, which is
not pasted in the issue body.

Promotion path:
- recover the original PoC from FuzzingBrain's archive of issue #945,
  pin to a commit before "Cleanup large rebase", and verify the leak
  fires there.
