// adp_brute.cpp — recover the 5-byte (40-bit) ADP/RC4 key for LWIN (KID 0x8) by
// brute force, using a single known-plaintext / known-ciphertext pair captured from
// an encrypted LDU1/LDU2 voice frame. Mirrors op25_crypt_adp.cc exactly: 5 key bytes,
// 8 cleartext MI bytes appended to form a 13-byte schedule, repeated over 256,
// RC4 KSA, then the 469-byte keystream.
//
// Build:  g++ -O3 -march=native -fopenmp -o adp_brute adp_brute.cpp
// Run:     ./adp_brute <mi_hex> <ct_hex> <pt_hex> [nthreads]
//   mi_hex: 9 MI bytes (72-bit MI), hex -- the MI that KEYED this codeword. In op25
//           the MI printed inside an LDU2 keys the *next* superframe, not the codewords
//           beneath it, so let scripts/extract_enc_pair.py pick it; don't eyeball the log.
//   ct_hex:  encrypted 11-byte codeword, hex
//   pt_hex:  expected plaintext for that codeword (11 bytes), hex
//
// Sharding (cross-machine):
//   ./adp_brute <mi> <ct> <pt> <nthreads> [--frame ldu1|ldu2] [--position P] [--start N] [--count M] [--found-file PATH]
//   --frame F:    ldu1 (keystream base 0) or ldu2 (base 101). Default ldu2.
//   --position P: the codeword's index WITHIN its own LDU (0..8), NOT a count of frames in
//                 the call -- op25 resets d_position to 0 in prepare() every LDU. The
//                 keystream offset is: base + P*11 + 267 (+2 when P==8):
//                    LDU1: 267, 278, 289, 300, 311, 322, 333, 344, 357
//                    LDU2: 368, 379, 390, 401, 412, 423, 434, 445, 458
//                 --position -1 is a back-compat alias for --frame ldu1 --position 0.
//                 extract_enc_pair.py prints the right --frame/--position for each pair.
//   --start N:  first key index to scan (default 0)
//   --count M:  how many keys this shard scans (default = 2^40 total)
//   --found-file PATH: optional shared file path. If present, the worker polls it each
//                       iteration; when another shard writes the key to that file, this
//                       shard aborts early. Use a path on a shared mount (NFS) so all
//                       machines see the same file.
//
// On success prints the 5 key bytes plus the LDU2 keystream prefix for cross-checking.

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <vector>
#include <algorithm>
#include <atomic>
#include <thread>
#include <chrono>
#include <string>
#include <omp.h>
#include <sys/stat.h>

static std::vector<uint8_t> hex_to_bytes(const char *s) {
    // Accept both space-separated ("63 3A 5A") and contiguous ("633A5A") hex.
    std::vector<uint8_t> out;
    const char *p = s;
    while (*p) {
        while (*p == ' ' || *p == ',') ++p;
        if (!*p) break;
        out.push_back((uint8_t)strtol(p, nullptr, 16));
        while (*p && *p != ' ' && *p != ',') ++p;
        while (*p == ' ' || *p == ',') ++p;
    }
    return out;
}

// Replicates op25_crypt_adp::prepare() byte-for-byte.
static bool try_key(const uint8_t key5[5], const uint8_t mi[8],
                    const uint8_t ct[11], const uint8_t pt[11], size_t offset) {
    uint8_t adp_key[13], S[256], K[256];
    for (int i = 0; i < 5; ++i) adp_key[i] = key5[i];
    for (int i = 5; i < 13; ++i) adp_key[i] = mi[i - 5];
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
    for (int n = 0; n < 11; ++n)
        if ((ct[n] ^ ks[offset + n]) != pt[n]) return false;
    return true;
}

// Parse optional flags from the remaining args after the three hex fields.
// Supports: [nthreads] [--start N] [--count M] [--found-file PATH]
// Returns (nthreads, start, count, found_file).
struct ShardOpts {
    int nthreads;
    uint64_t start;
    uint64_t count;
    std::string found_file;
    bool found_file_set;
    int position;
    std::string frame;   // "ldu1" (base 0) or "ldu2" (base 101)
};

static bool parse_flags(int argc, char **argv, int first, ShardOpts &o) {
    o.nthreads = std::thread::hardware_concurrency();
    o.start = 0;
    o.count = (1ULL << 40);
    o.found_file = "";
    o.found_file_set = false;
    o.position = 0;
    o.frame = "ldu2";
    for (int i = first; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--position") {
            if (i + 1 >= argc) { fprintf(stderr, "--position needs a value\n"); return false; }
            o.position = atoi(argv[++i]);
        } else if (a == "--frame") {
            if (i + 1 >= argc) { fprintf(stderr, "--frame needs a value (ldu1|ldu2)\n"); return false; }
            o.frame = argv[++i];
            if (o.frame != "ldu1" && o.frame != "ldu2") {
                fprintf(stderr, "--frame must be ldu1 or ldu2, got %s\n", o.frame.c_str());
                return false;
            }
        } else if (a == "--start") {
            if (i + 1 >= argc) { fprintf(stderr, "--start needs a value\n"); return false; }
            o.start = strtoull(argv[++i], nullptr, 10);
        } else if (a == "--count") {
            if (i + 1 >= argc) { fprintf(stderr, "--count needs a value\n"); return false; }
            o.count = strtoull(argv[++i], nullptr, 10);
        } else if (a == "--found-file") {
            if (i + 1 >= argc) { fprintf(stderr, "--found-file needs a value\n"); return false; }
            o.found_file = argv[++i];
            o.found_file_set = true;
        } else if (o.nthreads == (int)std::thread::hardware_concurrency() && i == first) {
            // First positional arg after the three hex fields = nthreads.
            o.nthreads = atoi(argv[i]);
            if (o.nthreads < 1) o.nthreads = 1;
        } else {
            fprintf(stderr, "unknown arg: %s\n", a.c_str());
            return false;
        }
    }
    return true;
}

// Check the shared found-file (cross-machine early abort). Returns true if another
// shard already found the key; if so, copies its 5 key bytes into out_key.
static bool found_file_seen(const std::string &path, uint8_t *out_key) {
    struct stat st;
    if (stat(path.c_str(), &st) != 0) return false;
    // File exists -> another shard wrote the key. Read 5 bytes.
    FILE *f = fopen(path.c_str(), "rb");
    if (!f) return false;
    uint8_t buf[5] = {0};
    size_t rd = fread(buf, 1, 5, f);
    fclose(f);
    if (rd >= 5) {
        for (int i = 0; i < 5; ++i) out_key[i] = buf[i];
        return true;
    }
    return false;
}

static void found_file_write(const std::string &path, const uint8_t key5[5]) {
    FILE *f = fopen(path.c_str(), "wb");
    if (!f) return;
    fwrite(key5, 1, 5, f);
    fclose(f);
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr,
            "usage: %s <mi_9bytes_hex> <ct_11bytes_hex> <pt_11bytes_hex> [nthreads] [--position P] [--start N] [--count M] [--found-file PATH]\n", argv[0]);
        return 1;
    }
    std::vector<uint8_t> mi_v = hex_to_bytes(argv[1]);
    std::vector<uint8_t> ct_v = hex_to_bytes(argv[2]);
    std::vector<uint8_t> pt_v = hex_to_bytes(argv[3]);
    if (mi_v.size() != 9) { fprintf(stderr, "MI must be 9 bytes (got %zu)\n", mi_v.size()); return 1; }
    if (ct_v.size() != 11) { fprintf(stderr, "ct must be 11 bytes (got %zu)\n", ct_v.size()); return 1; }
    if (pt_v.size() != 11) { fprintf(stderr, "pt must be 11 bytes (got %zu)\n", pt_v.size()); return 1; }

    ShardOpts opts;
    if (!parse_flags(argc, argv, 4, opts)) return 1;

    uint8_t mi[8];
    for (int i = 0; i < 8; ++i) mi[i] = mi_v[i];
    const uint8_t *ct = ct_v.data();
    const uint8_t *pt = pt_v.data();

    // op25_crypt_adp.cc keystream offset for FDMA voice codeword `position` (0..8)
    // of an LDU: offset = base + position*11 + 267 (+2 when position==8), where the
    // per-frame base is 0 for LDU1 and 101 for LDU2. position is the codeword index
    // WITHIN its own LDU (op25 resets d_position to 0 in prepare() each frame).
    //   LDU1: 267, 278, 289, 300, 311, 322, 333, 344, 357
    //   LDU2: 368, 379, 390, 401, 412, 423, 434, 445, 458
    // Back-compat: --position -1 is an alias for --frame ldu1 --position 0.
    if (opts.position == -1) { opts.frame = "ldu1"; opts.position = 0; }
    if (opts.position < 0 || opts.position > 8) {
        fprintf(stderr, "--position must be 0..8 (or -1 for LDU1 codeword 0), got %d\n", opts.position);
        return 1;
    }
    size_t base = (opts.frame == "ldu1") ? 0 : 101;
    size_t offset = base + (size_t)opts.position * 11 + 267 + (opts.position >= 8 ? 2 : 0);
    fprintf(stderr, "%s position %d -> keystream offset %zu\n",
            opts.frame.c_str(), opts.position, offset);

    const uint64_t TOTAL = 1ULL << 40;
    uint64_t start = opts.start;
    uint64_t count = opts.count;
    if (start + count > TOTAL) count = TOTAL - start;  // clamp to range
    if (start >= TOTAL) { fprintf(stderr, "--start out of range\n"); return 1; }

    fprintf(stderr, "ADP/RC4 brute force: shard [%llu .. %llu) of %llu total, %d threads\n",
            (unsigned long long)start, (unsigned long long)(start + count), (unsigned long long)TOTAL, opts.nthreads);

    std::atomic<bool> found(false);
    uint8_t found_key[5] = {0};
    bool remote_found = false;
    uint8_t remote_key[5] = {0};

    // Poll the shared found-file on a timer thread so other shards can abort early.
    if (opts.found_file_set) {
        std::thread([&]() {
            for (;;) {
                if (found_file_seen(opts.found_file, remote_key)) { remote_found = true; break; }
                std::this_thread::sleep_for(std::chrono::milliseconds(500));
                if (found.load(std::memory_order_relaxed)) break;
            }
        }).detach();
    }

    std::atomic<uint64_t> done(0);
    const uint64_t REPORT_EVERY = 1ULL << 20; // print progress every ~1M keys done
    #pragma omp parallel num_threads(opts.nthreads)
    {
        #pragma omp for schedule(dynamic, 100000)
        for (uint64_t k = start; k < start + count; ++k) {
            if (found.load(std::memory_order_relaxed) || remote_found) continue;
            uint8_t key5[5];
            uint64_t v = k;
            for (int i = 0; i < 5; ++i) { key5[i] = (uint8_t)(v & 0xFF); v >>= 8; }
            if (try_key(key5, mi, ct, pt, offset)) {
                for (int i = 0; i < 5; ++i) found_key[i] = key5[i];
                found = true;
                if (opts.found_file_set) found_file_write(opts.found_file, found_key);
            }
            uint64_t d = done.fetch_add(1, std::memory_order_relaxed) + 1;
            if (d % REPORT_EVERY == 0) {
                double pct = 100.0 * (double)d / (double)count;
                fprintf(stderr, "[progress] %llu / %llu keys done (%.1f%%)\n",
                        (unsigned long long)d, (unsigned long long)count, pct);
            }
        }
    }

    bool local_found = found.load();
    uint8_t *report_key = local_found ? found_key : remote_key;
    bool any_found = local_found || remote_found;

    if (any_found) {
        fprintf(stdout, "KEY FOUND: ");
        for (int i = 0; i < 5; ++i) fprintf(stdout, "%02x ", report_key[i]);
        fprintf(stdout, "\n");

        // Re-run KSA with the found key to print the LDU2 keystream prefix at the active offset.
        uint8_t adp_key[13], S[256], K[256];
        for (int i = 0; i < 5; ++i) adp_key[i] = report_key[i];
        for (int i = 5; i < 13; ++i) adp_key[i] = mi[i - 5];
        for (int i = 0; i < 256; ++i) K[i] = adp_key[i % 13];
        for (int i = 0; i < 256; ++i) S[i] = (uint8_t)i;
        int j = 0;
        for (int i = 0; i < 256; ++i) {
            j = (j + S[i] + K[i]) & 0xFF;
            std::swap(S[i], S[j]);
        }
        int ii = 0, jj = 0;
        uint8_t ks[469];
        for (int k2 = 0; k2 < 469; ++k2) {
            ii = (ii + 1) & 0xFF;
            jj = (jj + S[ii]) & 0xFF;
            std::swap(S[ii], S[jj]);
            ks[k2] = S[(S[ii] + S[jj]) & 0xFF];
        }
        fprintf(stdout, "keystream[%zu..%zu]: ", offset, offset + 31);
        for (int k2 = 0; k2 < 32; ++k2) fprintf(stdout, "%02x ", ks[offset + k2]);
        fprintf(stdout, "\n");
        return 0;
    }
    fprintf(stderr, "no key found in this shard [%llu .. %llu)\n",
            (unsigned long long)start, (unsigned long long)(start + count));
    return 2;
}
