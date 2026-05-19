// translit_fuzzer.cpp
// libFuzzer harness around the standalone reproducer from
// https://unicode-org.atlassian.net/browse/ICU-23365 — drives
// icu::Transliterator::createFromRules() with a fuzz-derived rules
// string. Reuses the input-shape logic of the upstream repro.cpp:
// data[0] picks direction, rest is a UTF-16 rules buffer.

#include <cstdint>
#include <cstddef>
#include <cstring>
#include <vector>

#include "unicode/translit.h"
#include "unicode/unistr.h"
#include "unicode/parseerr.h"
#include "unicode/utypes.h"

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 3) return 0;
    if (size > 4096) return 0;

    uint8_t dir = data[0] & 1;
    size_t unistr_size = (size - 1) / 2;
    std::vector<char16_t> buf(unistr_size);
    std::memcpy(buf.data(), data + 1, unistr_size * 2);
    icu::UnicodeString rules(false, buf.data(), unistr_size);

    UErrorCode status = U_ZERO_ERROR;
    UParseError pe;
    icu::Transliterator* t = icu::Transliterator::createFromRules(
        UNICODE_STRING_SIMPLE("test"), rules,
        dir ? UTRANS_FORWARD : UTRANS_REVERSE, pe, status);

    if (U_SUCCESS(status) && t) {
        icu::UnicodeString sample(u"Hello");
        t->transliterate(sample);
        delete t;
    }
    return 0;
}
