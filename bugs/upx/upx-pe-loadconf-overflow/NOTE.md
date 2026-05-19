# upx-pe-loadconf-overflow — deferred for v1

Build infrastructure works (Docker image fbbench/upx-pe-loadconf-overflow:v1
links `upx_main` via the same `objcopy --redefine-sym main=upx_orig_main`
trick used by upx-elf64-generate-overflow).

The blocking issue is **PoC reconstruction**: the bug lives behind UPX's
PE32 validator, which rejects malformed inputs early (`mem_size 2`,
`subsystem 0`, `file too small`, etc.). A successful PoC needs a
well-formed PE32 image (DOS stub + PE header + optional header + at
least one section) **plus** a Load Configuration data directory entry
with a bogus huge size. The 370-byte base64 PoC pasted in upstream
issue #950 is too small to pass UPX's file-size check; the full
working corpus input never appeared in the issue body.

To promote to shipped: synthesise a ~64 KiB PE32 (proper Subsystem,
SizeOfImage, section table, Load Config dir at idx 10) and verify it
reaches `processLoadConf()` before failing. Out of v1 scope.
