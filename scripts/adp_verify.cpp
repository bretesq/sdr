// adp_verify: confirm a 5-byte ADP key actually decrypts the captured ciphertext
// back to the known-plaintext. Uses the same op25_crypt_adp KSA as adp_brute.
//
// usage: adp_verify <mi_9bytes> <ct_11bytes> <pt_11bytes> <key_5bytes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <vector>
#include <algorithm>

static std::vector<uint8_t> hex_to_bytes(const char *s) {
    std::vector<uint8_t> out;
    const char *p = s;
    while (*p) {
        while (*p == ' ' || *p == ',' || *p == '\t') ++p;
        if (!*p) break;
        char *end = nullptr;
        unsigned long v = strtoul(p, &end, 16);
        out.push_back((uint8_t)v);
        if (end == p) break;
        p = end;
    }
    return out;
}

int main(int argc, char **argv) {
    if (argc < 5) {
        fprintf(stderr, "usage: %s <mi_9bytes_hex> <ct_11bytes_hex> <pt_11bytes_hex> <key_5bytes_hex>\n", argv[0]);
        return 1;
    }
    std::vector<uint8_t> mi_v = hex_to_bytes(argv[1]);
    std::vector<uint8_t> ct_v = hex_to_bytes(argv[2]);
    std::vector<uint8_t> pt_v = hex_to_bytes(argv[3]);
    std::vector<uint8_t> key_v = hex_to_bytes(argv[4]);
    if (mi_v.size() != 9 || ct_v.size() != 11 || pt_v.size() != 11 || key_v.size() != 5) {
        fprintf(stderr, "mi must be 9 bytes, ct/pt 11 bytes, key 5 bytes (got %zu/%zu/%zu/%zu)\n",
                mi_v.size(), ct_v.size(), pt_v.size(), key_v.size());
        return 1;
    }
    // Replicate op25_crypt_adp::prepare byte-for-byte (same as adp_brute try_key).
    uint8_t adp_key[13], S[256], K[256];
    for (int i = 0; i < 5; ++i) adp_key[i] = key_v[i];
    for (int i = 5; i < 13; ++i) adp_key[i] = mi_v[i - 5];
    for (int i = 0; i < 256; ++i) K[i] = adp_key[i % 13];
    for (int i = 0; i < 256; ++i) S[i] = (uint8_t)i;
    int j = 0;
    for (int i = 0; i < 256; ++i) {
        j = (j + S[i] + K[i]) & 0xFF;
        std::swap(S[i], S[j]);
    }
    int ii = 0, jj = 0;
    uint8_t ks[469];
    for (int k = 0; k < 469; ++k) {
        ii = (ii + 1) & 0xFF;
        jj = (jj + S[ii]) & 0xFF;
        std::swap(S[ii], S[jj]);
        ks[k] = S[(S[ii] + S[jj]) & 0xFF];
    }
    const size_t offset = 101 + 267; // LDU2, position 0, phase 1 FDMA
    bool match = true;
    std::vector<uint8_t> dec(11);
    for (int n = 0; n < 11; ++n) {
        dec[n] = ct_v[n] ^ ks[offset + n];
        if (dec[n] != pt_v[n]) match = false;
    }
    char buf[256];
    for (int n = 0; n < 11; ++n)
        sprintf(buf + n * 3, "%02x ", dec[n]);
    printf("decrypted: %s\n", buf);
    if (match) {
        printf("MATCH: key %02x %02x %02x %02x %02x correctly decrypts CT -> PT\n",
                key_v[0], key_v[1], key_v[2], key_v[3], key_v[4]);
        return 0;
    } else {
        printf("NO MATCH: decrypted bytes differ from known plaintext.\n");
        return 2;
    }
}
