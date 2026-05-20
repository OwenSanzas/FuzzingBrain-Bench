# All bugs (v1)

**37 bugs**, end-to-end gradeable by a deterministic oracle. The four
rightmost columns are the capability ladder — a ✅ means that flag is in the
bug's `K_b` and must fire (3-round unanimous) for the bug to PASS.

**R** reach · **C** crash · **Cl** class · **S** site &nbsp;—&nbsp; easy → hard.

| # | bug_id | project | lang | bug class | R | C | Cl | S |
|--:|---|---|:--:|---|:--:|:--:|:--:|:--:|
| 1 | [`avro-decompression-bomb`](https://github.com/apache/avro/pull/3625) | avro | Java | oom | · | ✅ | ✅ | · |
| 2 | [`avro-neg-block-size`](https://github.com/apache/avro/pull/3623) | avro | C | allocation-size-too-big | ✅ | ✅ | ✅ | ✅ |
| 3 | [`avro-neg-string-len`](https://github.com/apache/avro/pull/3622) | avro | C | allocation-size-too-big | ✅ | ✅ | ✅ | ✅ |
| 4 | [`binutils-rust-demangle-oom`](https://sourceware.org/bugzilla/show_bug.cgi?id=33878) | binutils | C | oom | ✅ | ✅ | ✅ | ✅ |
| 5 | [`dtc-fdt32-misalign`](https://github.com/dgibson/dtc/issues/178) | dtc | C | misaligned-access | ✅ | ✅ | ✅ | ✅ |
| 6 | [`freerdp-ntlm-memleak`](https://github.com/FreeRDP/FreeRDP/issues/12603) | freerdp | C | memory-leak | ✅ | · | ✅ | ✅ |
| 7 | [`fwupd-cab-mszip-bomb`](https://github.com/fwupd/fwupd/issues/9790) | fwupd | C | oom | · | ✅ | ✅ | · |
| 8 | [`fwupd-logitech-oob-read`](https://github.com/fwupd/fwupd/issues/9792) | fwupd | C | oob-read | · | ✅ | ✅ | ✅ |
| 9 | [`fwupd-logitech-stack-overflow`](https://github.com/fwupd/fwupd/issues/9779) | fwupd | C | stack-overflow | · | ✅ | ✅ | ✅ |
| 10 | [`fwupd-sbatlevel-underflow`](https://github.com/fwupd/fwupd/issues/9659) | fwupd | C | integer-underflow | · | ✅ | ✅ | · |
| 11 | [`ghidra-cplus-demangle-oom`](https://github.com/NationalSecurityAgency/ghidra/security/advisories/GHSA-m94m-fqr3-x442) | ghidra | C | oom | · | ✅ | ✅ | · |
| 12 | [`ghidra-rust-demangle-oom`](https://github.com/NationalSecurityAgency/ghidra/security/advisories/GHSA-m94m-fqr3-x442) | ghidra | C | oom | ✅ | ✅ | ✅ | ✅ |
| 13 | [`graaljs-illformed-locale`](https://github.com/oracle/graaljs/issues/985) | graaljs | Java | uncaught-exception | · | ✅ | ✅ | · |
| 14 | [`graaljs-regexlexer-oob`](https://github.com/oracle/graaljs/issues/986) | graaljs | Java | uncaught-exception | · | ✅ | ✅ | · |
| 15 | [`harfbuzz-fontations-oob-write`](https://github.com/harfbuzz/harfbuzz/issues/5946) | harfbuzz | C++ | oob-write | · | ✅ | ✅ | ✅ |
| 16 | [`icu-translit-rule-uaf`](https://unicode-org.atlassian.net/browse/ICU-23365) | icu | C++ | use-after-free | ✅ | · | ✅ | ✅ |
| 17 | [`imagemagick-msl-comment-npd`](https://github.com/ImageMagick/ImageMagick/security/advisories/GHSA-5vx3-wx4q-6cj8) | imagemagick | C++ | null-deref | ✅ | ✅ | · | ✅ |
| 18 | [`imagemagick-msl-stack-overflow`](https://github.com/ImageMagick/ImageMagick/security/advisories/GHSA-9vj4-wc7r-p844) | imagemagick | C | stack-overflow | · | ✅ | ✅ | ✅ |
| 19 | [`jq-dump-op-npd`](https://github.com/jqlang/jq/issues/3458) | jq | C | null-deref | ✅ | ✅ | ✅ | ✅ |
| 20 | [`jsonjava-jsonml-classcast`](https://github.com/stleary/JSON-java/issues/1034) | json-java | Java | uncaught-exception | ✅ | ✅ | ✅ | ✅ |
| 21 | [`jsonjava-unescape-numformat`](https://github.com/stleary/JSON-java/issues/1036) | json-java | Java | uncaught-exception | ✅ | ✅ | ✅ | ✅ |
| 22 | [`jsonjava-unescape-strindex`](https://github.com/stleary/JSON-java/issues/1035) | json-java | Java | uncaught-exception | ✅ | ✅ | ✅ | ✅ |
| 23 | [`libavif-jni-signext`](https://github.com/AOMediaCodec/libavif/issues/3177) | libavif | C++ | heap-buffer-overflow | · | ✅ | ✅ | ✅ |
| 24 | [`mongoose-mg-match-overflow`](https://github.com/cesanta/mongoose/issues/3393) | mongoose | C | heap-buffer-overflow | ✅ | ✅ | ✅ | ✅ |
| 25 | [`ndpi-hex-decode-sscanf`](https://github.com/ntop/nDPI/issues/3159) | ndpi | C | oob-read | ✅ | ✅ | ✅ | ✅ |
| 26 | [`netsnmp-vacm-parse-npd`](https://github.com/net-snmp/net-snmp/issues/1052) | net-snmp | C | null-deref | ✅ | ✅ | ✅ | ✅ |
| 27 | [`opencv-yaml-parsekey`](https://github.com/opencv/opencv/issues/28619) | opencv | C++ | heap-buffer-overflow | · | ✅ | ✅ | ✅ |
| 28 | [`openldap-ldif-stack-underflow`](https://bugs.openldap.org/show_bug.cgi?id=10431) | openldap | C | stack-buffer-underflow | ✅ | ✅ | ✅ | ✅ |
| 29 | [`openldap-parse-whsp`](https://bugs.openldap.org/show_bug.cgi?id=10430) | openldap | C | heap-buffer-overflow | ✅ | ✅ | ✅ | ✅ |
| 30 | [`openssl-des-ofb-cfb-overread`](https://github.com/openssl/openssl/issues/30284) | openssl | C | stack-buffer-overread | ✅ | ✅ | ✅ | ✅ |
| 31 | [`ots-processgeneric-npd`](https://github.com/khaledhosny/ots/issues/308) | ots | C++ | null-deref | ✅ | ✅ | ✅ | ✅ |
| 32 | [`pdfbox-cmap-bfrange-aioob`](https://github.com/apache/pdfbox/pull/411) | pdfbox | Java | uncaught-exception | ✅ | ✅ | ✅ | ✅ |
| 33 | [`pdfbox-inlineimage-type-confusion`](https://github.com/apache/pdfbox/pull/410) | pdfbox | Java | type-confusion | ✅ | ✅ | ✅ | ✅ |
| 34 | [`pdfbox-pfb-negative-array`](https://github.com/apache/pdfbox/pull/412) | pdfbox | Java | uncaught-exception | ✅ | ✅ | ✅ | ✅ |
| 35 | [`simdutf-utf16-utf8-overflow`](https://github.com/simdutf/simdutf/issues/911) | simdutf | C++ | heap-buffer-overflow | ✅ | ✅ | ✅ | ✅ |
| 36 | [`upx-elf32-pack2-memleak`](https://github.com/upx/upx/issues/945) | upx | C++ | memory-leak | ✅ | · | ✅ | ✅ |
| 37 | [`upx-elf64-generate-overflow`](https://github.com/upx/upx/issues/947) | upx | C++ | heap-buffer-overflow | ✅ | ✅ | ✅ | ✅ |

Each row links to its upstream report. Full description: `./fb-bench show <bug_id>`. Per-bug `K_b` lives in `bugs/<project>/<bug_id>/bench.yaml`.

