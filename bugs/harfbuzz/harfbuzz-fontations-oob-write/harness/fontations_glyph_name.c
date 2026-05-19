// fontations_glyph_name.c
// libFuzzer wrapper around the upstream repro.c from
// https://github.com/harfbuzz/harfbuzz/issues/5946.
//
// Bug: hb_font_get_glyph_name(font, 0, buf, 0) with the fontations
// font_funcs backend writes past the end of `buf` because the
// fontations glue assumes `size > 0`.
//
// Input layout: raw font bytes (a TTF/OTF). Every well-formed input
// hits the OOB write; malformed inputs are rejected by hb_face_create.

#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include <hb.h>
#include <hb-fontations.h>

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 4 || size > (4u << 20)) return 0;

    hb_blob_t *blob = hb_blob_create((const char *)data, (unsigned)size,
                                     HB_MEMORY_MODE_READONLY, NULL, NULL);
    if (!blob) return 0;

    hb_face_t *face = hb_face_create(blob, 0);
    hb_font_t *font = hb_font_create(face);
    hb_fontations_font_set_funcs(font);   /* the buggy backend */

    char buf[1];
    hb_font_get_glyph_name(font, 0, buf, 0);   /* size == 0 -> OOB write */

    hb_font_destroy(font);
    hb_face_destroy(face);
    hb_blob_destroy(blob);
    return 0;
}
