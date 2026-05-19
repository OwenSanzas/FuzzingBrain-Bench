// utils.cc — minimal OSS-Fuzz ImageMagick helper.
// Upstream `utils.cc` from the ImageMagick OSS-Fuzz harness pack is not
// published with the advisory; reproducing just the predicate that
// every MSL/SVG/etc harness imports.
#ifndef FB_IMAGEMAGICK_UTILS_CC
#define FB_IMAGEMAGICK_UTILS_CC

#include <cstddef>

static inline bool IsInvalidSize(const size_t size, const size_t min_size = 0) {
    if (size < min_size) return true;
    if (size > 16 * 1024 * 1024) return true;  // 16 MiB upper bound
    return false;
}

#endif
