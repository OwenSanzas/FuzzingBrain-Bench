#!/usr/bin/env python3
"""Generate PoC for graaljs-illformed-locale.

Verbatim from upstream issue #985: malformed locale string
"te-lema4e-alema". The invalid variant subtag "4e-alema" violates
BCP 47, but the pre-fix GraalJS treats ICU's IllformedLocaleException
as an InternalError instead of a JS RangeError.
"""

def create_poc(output='poc.bin'):
    with open(output, 'wb') as f:
        f.write(b'te-lema4e-alema')
    print(f"wrote {output} (15 bytes)")


if __name__ == '__main__':
    create_poc()
