# Why this directory has a stubs.c

The user-authored `fuzzer.c` opens with:

```c
#define MG_ENABLE_SOCKET 0
#define MG_ENABLE_LOG 0
#include "mongoose.c"
```

The intent is "compile mongoose without its networking backends so the
harness only exercises pure helpers like `mg_match`".

At the bug's vulnerable commit (`b313d697...`), however, several
mongoose modules — WebSocket, DNS, the mgr poll loop — still
reference networking symbols (`mg_send`, `mg_connect_resolved`,
`mg_multicast_add`, `mg_open_listener`, `mg_mgr_poll`)
**unconditionally**. Setting `MG_ENABLE_SOCKET=0` compiles those
modules without their bodies but does not remove the references, so
the linker fails with "undefined reference" for those five symbols.

`stubs.c` is a separate translation unit that provides no-op
implementations for exactly those five symbols. They are **never
executed at runtime** by anything reachable from the `mg_match`
fuzz target; their only role is to satisfy the linker.

This means:

- **`fuzzer.c` is unchanged from the version the user pasted in.**
- The benchmark still measures whether an agent can drive `mg_match`
  to its OOB read; sockets / DNS / mgr are not in scope.
- Reviewers can verify the stubs are dead by `nm` / `objdump` on the
  built binary: none of the five symbols sits on a reachable call
  path from `LLVMFuzzerTestOneInput`.
