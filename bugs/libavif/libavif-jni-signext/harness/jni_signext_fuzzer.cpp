// Native-side reproducer for libavif-jni-signext (issue #3177).
//
// The Android JNI shim libavif_jni.cc has:
//
//   FUNC(jboolean, getInfo, jobject encoded, int length, jobject info) {
//     ...
//     CreateDecoderAndParse(&decoder, buffer, length, 1);   // length is jint
//   }
//   bool CreateDecoderAndParse(..., int length, ...) {
//     ...
//     avifDecoderSetIOMemory(decoder->decoder, buffer, length);  // length -> size_t
//   }
//
// When Java calls AvifDecoder.getInfo(buf, -1, info), `length=-1` (int) is
// implicitly sign-extended to size_t SIZE_MAX inside avifDecoderSetIOMemory.
// avifDecoderParse() then reads off the actual buffer, producing a heap
// buffer overflow.
//
// Native repro: do exactly that — set IO memory to a small valid buffer with
// claimed size SIZE_MAX, parse, observe ASan heap-buffer-overflow.
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <avif/avif.h>

extern "C" int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 4 || size > (4u << 20)) return 0;

    avifDecoder *decoder = avifDecoderCreate();
    if (!decoder) return 0;

    decoder->maxThreads = 1;
    decoder->ignoreXMP = AVIF_TRUE;
    decoder->ignoreExif = AVIF_TRUE;
    decoder->strictFlags = 0;

    // Make a copy of the input and tell libavif it's SIZE_MAX bytes long.
    // (Mirrors what the JNI bug does when Java passes int length = -1.)
    uint8_t *buffer = (uint8_t *) malloc(size);
    if (!buffer) {
        avifDecoderDestroy(decoder);
        return 0;
    }
    memcpy(buffer, data, size);

    // The sign-extension bug — pass SIZE_MAX as the buffer length.
    avifResult res = avifDecoderSetIOMemory(decoder, buffer, (size_t) -1);
    if (res == AVIF_RESULT_OK) {
        (void) avifDecoderParse(decoder);  // walks off the end of `buffer`
    }

    avifDecoderDestroy(decoder);
    free(buffer);
    return 0;
}
