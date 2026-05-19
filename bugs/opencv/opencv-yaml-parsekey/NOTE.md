# opencv-yaml-parsekey — deferred for v1

OpenCV 4.x master (cloned at v1 build time) appears to already include
a fix for issue #28619: the malformed YAML in the upstream PoC now
raises a clean `cv::Exception("Parsing error (parseKey)")` rather than
NULL-deref'ing, and our harness's `try { ... } catch (cv::Exception&)`
swallows it.

To ship this bug we would need to pin the vuln_commit to a specific
opencv 4.x SHA *before* the fix landed and (re)build against that.
The bench-corpus.json record has `vuln_commit: null` (the bug was
disclosed 2026-05-12 and the issue history doesn't yet point at a
clear pre-fix SHA), so the pin requires manual investigation.

The Dockerfile, harness, and PoC are kept here for the eventual
promotion. They build a minimal opencv (`BUILD_LIST=core` + most
WITH_* off) so the iteration cost is moderate, ~5 min.
