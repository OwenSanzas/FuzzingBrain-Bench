# upx-pe-resource-memleak — partial build infra, deferred

Build infra works (Dockerfile reuses the upx-elf64-generate-overflow
recipe). Image builds cleanly at vuln_commit `1ebd3356`.

Same status as upx-elf32-pack2-memleak:
- bench-corpus.json: `vuln_commit: null`.
- upstream issue #946 closed by maintainer with "Done", no fix commit
  pasted, no PoC source in the issue body (only the harness name
  `test_packed_file_fuzzer` is referenced).

The pack-file fuzzer is identical to the upx-elf64-generate-overflow
harness (`pack_file_fuzzer.cpp`); promotion needs the original PE32
PoC + a commit predating the silent fix.
