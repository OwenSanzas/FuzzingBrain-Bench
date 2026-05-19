// des_ofb_fuzzer.c
//
// libFuzzer harness around the OpenSSL DES-OFB / DES-CFB64 cipher contexts.
// The bug: EVP_CIPHER_CTX_set_params accepts OSSL_CIPHER_PARAM_NUM without
// validating against the per-cipher block-size bound. Encrypt thus reads
// past the 8-byte DES_cblock on the stack.
//
// Input layout:
//   data[0]      : cipher selector (0=DES-EDE3-OFB, 1=DES-EDE3-CFB)
//   data[1]      : the "num" parameter to set
//   data[2..33]  : 32 bytes of plaintext  (zero-padded if short)
//
// Anything shorter than 2 bytes is rejected up-front.

#include <stdint.h>
#include <stddef.h>
#include <string.h>

#include <openssl/evp.h>
#include <openssl/params.h>
#include <openssl/core_names.h>

static const char *CIPHERS[] = { "DES-EDE3-OFB", "DES-EDE3-CFB" };

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 2) return 0;

    const char *name = CIPHERS[data[0] & 1];
    unsigned int bad_num = data[1];

    unsigned char key[24] = {0};
    unsigned char iv[8]   = {0};
    unsigned char in[32]  = {0};
    unsigned char out[64] = {0};
    int outlen = 0;

    size_t copy_size = size > 2 ? (size - 2 > sizeof(in) ? sizeof(in) : size - 2) : 0;
    if (copy_size) memcpy(in, data + 2, copy_size);

    EVP_CIPHER *cipher = EVP_CIPHER_fetch(NULL, name, NULL);
    if (!cipher) return 0;
    EVP_CIPHER_CTX *ctx = EVP_CIPHER_CTX_new();
    if (!ctx) { EVP_CIPHER_free(cipher); return 0; }

    if (EVP_EncryptInit_ex2(ctx, cipher, key, iv, NULL) == 1) {
        OSSL_PARAM params[2];
        params[0] = OSSL_PARAM_construct_uint(OSSL_CIPHER_PARAM_NUM, &bad_num);
        params[1] = OSSL_PARAM_construct_end();
        EVP_CIPHER_CTX_set_params(ctx, params);
        (void)EVP_EncryptUpdate(ctx, out, &outlen, in, (int)copy_size);
    }

    EVP_CIPHER_CTX_free(ctx);
    EVP_CIPHER_free(cipher);
    return 0;
}
