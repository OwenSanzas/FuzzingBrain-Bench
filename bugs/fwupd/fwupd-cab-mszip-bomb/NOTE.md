# fwupd-cab-mszip-bomb — partial build infra, deferred

Build attempt status (2026-05-19):
- Dockerfile pulls fwupd 2.0.18 + Debian system deps
  (glib2, json-glib, libxmlb, libcurl4, libgudev, libgusb, polkit, etc.)
- meson configures fwupd with most plugins disabled
- BUT: meson auto-downloads libxmlb as a subproject when the Debian
  system libxmlb-dev is too old, and the subproject requires
  gobject-introspection-1.0 which we explicitly disable at fwupd level.

This means a clean build needs one of:
- newer libxmlb (>= what fwupd 2.0.18 wants) packaged or built from a
  pinned source
- gobject-introspection-1.0-dev installed (which then pulls more deps)
- run fwupd's `contrib/ci/oss-fuzz.py` which builds glib/json-glib/
  libxmlb from pinned sources (~30 min, large image footprint).

For v1 we defer — none of the four fwupd bugs ship. The bench-corpus
description and the libfwupdplugin template fuzzer (.c.in) are
adequate for the bench definition; only the binary harness is missing.

Promotion path: switch to oss-fuzz.py, or install
`gobject-introspection libgirepository1.0-dev` in the apt list and
let the libxmlb subproject build go through.
