# Provenance

**Bug**: OOB read at d[n] in DES_ede3_ofb64_encrypt when num >= 8
**Upstream**: https://github.com/openssl/openssl/issues/30284
**Vuln commit**: 22be3f1b8e3d8d09de89794a6d59f00176b32b2d

## Harness

`harness/des_ofb_fuzzer.c` is a libFuzzer wrapper using the live
OpenSSL EVP API (not the synthetic standalone reproducer). Input
layout:

    data[0]      cipher selector (0=DES-EDE3-OFB, 1=DES-EDE3-CFB)
    data[1]      OSSL_CIPHER_PARAM_NUM value (bug fires for >= 8)
    data[2..]    up to 32 bytes of plaintext

Drives:
- EVP_CIPHER_fetch + EVP_CIPHER_CTX_new
- EVP_EncryptInit_ex2 with zero key/iv
- EVP_CIPHER_CTX_set_params({NUM=bad_num})
- EVP_EncryptUpdate

## Triggering input

`poc/poc.bin` is 18 bytes: `0x00 0x08` + 16 'A' bytes. UBSan fires
exactly at `crypto/des/ofb64ede.c:58:30` (`d[n]` with n=8 indexing an
8-byte DES_cblock).

## Build

`./Configure linux-x86_64 no-shared no-tests no-asm` then build only
`libcrypto.a`. Skipping `no-asm` and tests keeps the build under a
minute on this machine.
