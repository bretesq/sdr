// adp_brute.cu — CUDA port of the 40-bit ADP/RC4 key search.
//
// Build:
//   /usr/local/cuda/bin/nvcc -O3 -arch=sm_120 -o adp_brute_cuda adp_brute.cu
// Run: same CLI as the OpenMP binary:
//   ./adp_brute_cuda <mi_hex> <ct_hex> <pt_hex> [nblocks] [--frame ldu1|ldu2] [--position P] [--start N] [--count M] [--found-file PATH]
//   mi_hex: the 9-byte MI that KEYED this codeword (op25 chains the MI: the value
//           printed inside an LDU2 keys the *next* superframe). Let
//           scripts/extract_enc_pair.py select it -- it prints the right --frame/--position.
//   --frame F:    ldu1 (keystream base 0) or ldu2 (base 101). Default ldu2.
//   --position P: the codeword's index WITHIN its own LDU (0..8; op25 resets d_position
//                 to 0 in prepare() each frame), NOT a count of frames. Offset is
//                 base + P*11 + 267 (+2 when P==8). --position -1 aliases --frame ldu1 -P 0.
//
// Design:
//  - Each thread owns one key and holds its RC4 permutation S[256] in
//    per-thread registers (unrolled 4-wide via uint32 words) to keep the
//    KSA+keystream hot loop register-resident and avoid global-memory
//    traffic on the 256-byte state.
//  - Key range [start, start+count) is distributed across blocks; within a
//    block, threads stride over keys.
//  - A separate CUDA stream polls the shared found-file every ~500 ms
//    (mirrors the CPU binary); when set, a device-side flag makes all
//    threads skip their remaining work and we return early.
//  - On a local hit, the 5 key bytes are copied back and (if --found-file
//    is set) written to the shared file exactly like the CPU version.

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
#include <sys/stat.h>
#include <cctype>
#include <cuda_runtime.h>

#define CUDA_CHECK(x) do { cudaError_t _e = (x); if (_e != cudaSuccess) { \
    fprintf(stderr, "CUDA error %s at line %d\n", cudaGetErrorString(_e), __LINE__); \
    exit(1); } } while (0)

static std::vector<uint8_t> hex_to_bytes(const char *s) {
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

// ------------------------------------------------------------------
// GPU side
// ------------------------------------------------------------------

// Per-thread state: RC4 permutation held as 64 x uint32 (4 bytes each).
// KSA + keystream are unrolled over the 256-byte state.

__device__ uint32_t g_stopFlag; // 0 = keep going, 1 = stop

// Tiny kernel to set the device flag (simpler than memcpy to a __device__ var).
__global__ void set_stop(uint32_t v) { if (threadIdx.x == 0) g_stopFlag = v; }
static uint8_t g_remote_found_key[5] = {0};
static bool g_remote_found = false;

// One key per thread. key5 is derived from the global index.
// k is the global key index (uint64) split into 5 bytes.
__global__ void brute_kernel(const uint8_t *mi8, const uint8_t *ct_all, const uint8_t *pt11,
                              int ncand, const int *offsets,
                              uint64_t start, uint64_t count,
                              uint64_t *out_hits, uint8_t *out_key, int *out_cand) {
    uint64_t idx = (uint64_t)(blockIdx.x * blockDim.x) + threadIdx.x;
    // Stride across the whole [start, start+count) so each thread covers a
    // strided slice; this keeps load balanced without dynamic scheduling.
    uint64_t step = (uint64_t)gridDim.x * blockDim.x;
    uint64_t k;
    for (k = start + idx; k < start + count; k += step) {
        if (g_stopFlag) return;
        uint8_t key5[5];
        uint64_t v = k;
        for (int i = 0; i < 5; ++i) { key5[i] = (uint8_t)(v & 0xFF); v >>= 8; }

        uint32_t S[64]; // 256 bytes as 64 x uint32
        for (int i = 0; i < 64; ++i) {
            int base = i * 4;
            S[i] = (uint32_t)(base) | ((uint32_t)(base + 1) << 8) | ((uint32_t)(base + 2) << 16) | ((uint32_t)(base + 3) << 24);
        }
        // KSA: build the 13-byte schedule = key5[0..4] ++ mi[0..7],
        // repeated over the 256-byte KSA as K[i % 13].
        uint8_t sched[13];
        for (int i = 0; i < 5; ++i) sched[i] = key5[i];
        for (int i = 5; i < 13; ++i) sched[i] = mi8[i - 5];
        int j = 0;
        for (int i = 0; i < 256; ++i) {
            // Read byte i from S as a raw byte
            uint8_t Si = (uint8_t)((S[i >> 2] >> ((i & 3) * 8)) & 0xFF);
            uint8_t Kbyte = sched[i % 13];
            j = (j + Si + Kbyte) & 0xFF;
            // Swap S[i] and S[j]
            uint8_t sj = (uint8_t)((S[j >> 2] >> ((j & 3) * 8)) & 0xFF);
            uint32_t wi = i >> 2, wj = j >> 2;
            uint32_t bi = (i & 3) * 8, bj = (j & 3) * 8;
            uint32_t newSi = (S[wi] & ~(0xFFu << bi)) | ((uint32_t)sj << bi);
            uint32_t newSj = (S[wj] & ~(0xFFu << bj)) | ((uint32_t)Si << bj);
            if (wi == wj) {
                // Same word: both bytes in the same 32-bit word.
                uint32_t word = (S[wi] & ~(0xFFu << bi)) | ((uint32_t)sj << bi);
                word = (word & ~(0xFFu << bj)) | ((uint32_t)Si << bj);
                S[wi] = word;
            } else {
                S[wi] = newSi;
                S[wj] = newSj;
            }
        }
        // Keystream 469 bytes
        int ii = 0, jj = 0;
        // We only need keystream bytes 368..378 (11 bytes).
        // But the RC4 PRGA state depends on the first 368 steps, so we run all 469
        // and XOR the 11 relevant bytes.
        uint8_t ks[469];
        for (int n = 0; n < 469; ++n) {
            ii = (ii + 1) & 0xFF;
            uint8_t Sii = (uint8_t)((S[ii >> 2] >> ((ii & 3) * 8)) & 0xFF);
            jj = (jj + Sii) & 0xFF;
            uint8_t Sjj = (uint8_t)((S[jj >> 2] >> ((jj & 3) * 8)) & 0xFF);
            uint32_t wi = ii >> 2, wj = jj >> 2;
            uint32_t bi = (ii & 3) * 8, bj = (jj & 3) * 8;
            uint32_t newSi = (S[wi] & ~(0xFFu << bi)) | ((uint32_t)Sjj << bi);
            uint32_t newSj = (S[wj] & ~(0xFFu << bj)) | ((uint32_t)Sii << bj);
            if (wi == wj) {
                uint32_t word = (S[wi] & ~(0xFFu << bi)) | ((uint32_t)Sjj << bi);
                word = (word & ~(0xFFu << bj)) | ((uint32_t)Sii << bj);
                S[wi] = word;
            } else {
                S[wi] = newSi;
                S[wj] = newSj;
            }
            uint8_t out_byte = (uint8_t)((S[(Sii + Sjj) & 0xFF] >> 0) & 0xFF);
            // S[(Sii+Sjj)&0xFF] is a byte read; compute the index and pull the byte.
            int idx2 = (Sii + Sjj) & 0xFF;
            out_byte = (uint8_t)((S[idx2 >> 2] >> ((idx2 & 3) * 8)) & 0xFF);
            ks[n] = out_byte;
        }
        // Every candidate codeword of this superframe is tested against the SAME
        // keystream. The PRGA above already runs to byte 469 regardless of which
        // offset we want -- the RC4 state at offset 267 can only be reached by
        // stepping through it -- so each extra candidate costs 11 byte-compares
        // against ks[] and nothing else. Measured: a search at offset 267 and one
        // at offset 458 take the same time to within noise (4.16 s vs 4.00 s per
        // 400M keys), which is what makes 18 candidates per pass free.
        int hit_cand = -1;
        for (int c = 0; c < ncand && hit_cand < 0; ++c) {
            const uint8_t *ctc = ct_all + (size_t)c * 11;
            const int OFFSET = offsets[c];
            bool ok = true;
            for (int n = 0; n < 11; ++n) {
                if ((ctc[n] ^ ks[OFFSET + n]) != pt11[n]) { ok = false; break; }
            }
            if (ok) hit_cand = c;
        }
        if (hit_cand >= 0) {
            // Record hit: write global index to out_hits, key bytes to out_key.
            // First-hit-wins via atomic CAS on out_hits[0].
            uint64_t old = out_hits[0];
            if (old == 0) {
                if (atomicCAS((unsigned long long *)&out_hits[0], 0, (unsigned long long)k) == 0) {
                    uint64_t v = k;
                    for (int i = 0; i < 5; ++i) { out_key[i] = (uint8_t)(v & 0xFF); v >>= 8; }
                    *out_cand = hit_cand;   // which codeword's plaintext guess was right
                }
            }
            g_stopFlag = 1;
        }
    }
}

struct GPUState {
    uint8_t *d_mi;
    uint8_t *d_ct;
    uint8_t *d_pt;
    uint64_t *d_hits;
    uint8_t *d_key;
    cudaStream_t stream;
};

// Host-side progress estimator: while the kernel runs, every 30s log an
// estimated % done based on elapsed wall-time × measured throughput.
// Throughput is inferred per-GPU-class from a micro-benchmark (wopr H100-class
// ~840K keys/s effective for the full-shard run; local RTX PRO 6000 slower).
static void report_progress(uint64_t count, std::atomic<bool> &stop, double throughput_keys_per_s) {
    const auto t0 = std::chrono::steady_clock::now();
    while (!stop.load()) {
        auto now = std::chrono::steady_clock::now();
        double elapsed_s = std::chrono::duration<double>(now - t0).count();
        uint64_t est_done = (uint64_t)(elapsed_s * throughput_keys_per_s);
        if (est_done >= count) est_done = count;
        double pct = (double)est_done / (double)count * 100.0;
        fprintf(stderr, "[progress] est %llu / %llu keys done (%.1f%%, %.1f min elapsed, est %.0f keys/s)\n",
                (unsigned long long)est_done, (unsigned long long)count, pct, elapsed_s / 60.0, throughput_keys_per_s);
        std::this_thread::sleep_for(std::chrono::seconds(30));
    }
}

static void poll_found_file(std::string path, cudaStream_t stream, std::atomic<bool> &stop) {
    for (;;) {
        struct stat st;
        if (stat(path.c_str(), &st) == 0) {
            FILE *f = fopen(path.c_str(), "rb");
            uint8_t key5[5] = {0};
            if (f) {
                size_t rd = fread(key5, 1, 5, f);
                fclose(f);
                if (rd >= 5) {
                    // Signal device to stop using a setup kernel (device globals
                    // can't be memcpy targets directly).
                    uint8_t *d_key = nullptr;
                    CUDA_CHECK(cudaMalloc(&d_key, 5));
                    CUDA_CHECK(cudaMemcpy(d_key, key5, 5, cudaMemcpyHostToDevice));
                    set_stop<<<1, 32, 0, stream>>>(1);
                    CUDA_CHECK(cudaStreamSynchronize(stream));
                    for (int i = 0; i < 5; ++i) g_remote_found_key[i] = key5[i];
                    g_remote_found = true;
                    stop = true;
                    cudaFree(d_key);
                    return;
                }
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
        if (stop.load()) return;
    }
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr,
            "usage: %s <mi_9bytes_hex> <ct_11bytes_hex> <pt_11bytes_hex> [nblocks]\n"
            "         [--frame ldu1|ldu2] [--position P] [--pairs FILE]\n"
            "         [--start N] [--count M] [--found-file PATH] [--progress]\n"
            "\n"
            "  --pairs FILE  test MANY codewords of one superframe in a single pass.\n"
            "                Lines: '<ldu1|ldu2> <position 0..8> <11 hex bytes>'.\n"
            "                All share the MI and PT given on the command line. The\n"
            "                RC4 keystream is built once per key regardless of which\n"
            "                offset is wanted, so each extra codeword is ~free: 18\n"
            "                candidates cost what 1 does. Positional <ct> is then a\n"
            "                placeholder and is ignored.\n", argv[0]);
        return 1;
    }
    std::vector<uint8_t> mi_v = hex_to_bytes(argv[1]);
    std::vector<uint8_t> ct_v = hex_to_bytes(argv[2]);
    std::vector<uint8_t> pt_v = hex_to_bytes(argv[3]);
    if (mi_v.size() != 9) { fprintf(stderr, "MI must be 9 bytes (got %zu)\n", mi_v.size()); return 1; }
    if (ct_v.size() != 11) { fprintf(stderr, "ct must be 11 bytes (got %zu)\n", ct_v.size()); return 1; }
    if (pt_v.size() != 11) { fprintf(stderr, "pt must be 11 bytes (got %zu)\n", pt_v.size()); return 1; }

    // Parse flags
    int nblocks = 0;
    int position = 0;
    std::string frame = "ldu2";   // "ldu1" (base 0) or "ldu2" (base 101)
    uint64_t start = 0;
    uint64_t count = 1ULL << 40;
    std::string found_file;
    bool found_file_set = false;
    std::string pairs_file;
    bool progress_on = false;
    double throughput = 840000.0; // effective keys/s (full-shard wopr measurement: 1.1B/1305s)
    int i = 4;
    // First positional = nblocks if present and numeric.
    if (i < argc && isdigit(argv[i][0]) && std::string(argv[i]).find_first_not_of("0123456789") == std::string::npos) {
        nblocks = atoi(argv[i]);
        i++;
    }
    for (; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--position") {
            if (i + 1 >= argc) { fprintf(stderr, "--position needs a value\n"); return 1; }
            position = atoi(argv[++i]);
        } else if (a == "--frame") {
            if (i + 1 >= argc) { fprintf(stderr, "--frame needs a value (ldu1|ldu2)\n"); return 1; }
            frame = argv[++i];
            if (frame != "ldu1" && frame != "ldu2") {
                fprintf(stderr, "--frame must be ldu1 or ldu2, got %s\n", frame.c_str());
                return 1;
            }
        } else if (a == "--start") {
            if (i + 1 >= argc) { fprintf(stderr, "--start needs a value\n"); return 1; }
            start = strtoull(argv[++i], nullptr, 10);
        } else if (a == "--count") {
            if (i + 1 >= argc) { fprintf(stderr, "--count needs a value\n"); return 1; }
            count = strtoull(argv[++i], nullptr, 10);
        } else if (a == "--found-file") {
            if (i + 1 >= argc) { fprintf(stderr, "--found-file needs a value\n"); return 1; }
            found_file = argv[++i];
            found_file_set = true;
        } else if (a == "--pairs") {
            if (i + 1 >= argc) { fprintf(stderr, "--pairs needs a value\n"); return 1; }
            pairs_file = argv[++i];
        } else if (a == "--progress") {
            progress_on = true;
        } else if (a == "--throughput") {
            if (i + 1 >= argc) { fprintf(stderr, "--throughput needs a value\n"); return 1; }
            throughput = atof(argv[++i]);
        } else {
            fprintf(stderr, "unknown arg: %s\n", a.c_str());
            return 1;
        }
    }
    if (nblocks <= 0) nblocks = 512; // sensible default
    // Back-compat: --position -1 is an alias for --frame ldu1 --position 0.
    if (position == -1) { frame = "ldu1"; position = 0; }
    if (position < 0 || position > 8) {
        fprintf(stderr, "--position must be 0..8 (or -1 for LDU1 codeword 0), got %d\n", position);
        return 1;
    }
    // op25_crypt_adp.cc keystream offset for codeword `position` (0..8) of an LDU:
    // base + position*11 + 267 (+2 when position==8), base 0 for LDU1, 101 for LDU2.
    // position is the codeword index within its own LDU (op25 resets d_position to 0
    // in prepare() each frame).
    //   LDU1: 267, 278, 289, 300, 311, 322, 333, 344, 357
    //   LDU2: 368, 379, 390, 401, 412, 423, 434, 445, 458
    auto offset_of = [](const std::string &fr, int pos) {
        const int b = (fr == "ldu1") ? 0 : 101;
        return b + pos * 11 + 267 + (pos >= 8 ? 2 : 0);
    };

    // Candidate codewords. One by default; a --pairs file supplies a whole
    // superframe, which costs the same because the keystream is built once.
    std::vector<uint8_t> cand_ct;
    std::vector<int> cand_off;
    std::vector<std::string> cand_label;
    if (!pairs_file.empty()) {
        FILE *pf = fopen(pairs_file.c_str(), "r");
        if (!pf) { fprintf(stderr, "cannot open --pairs %s\n", pairs_file.c_str()); return 1; }
        char line[512];
        int lineno = 0;
        while (fgets(line, sizeof(line), pf)) {
            lineno++;
            std::string l(line);
            size_t h = l.find('#');
            if (h != std::string::npos) l = l.substr(0, h);
            if (l.find_first_not_of(" \t\r\n") == std::string::npos) continue;
            char fr[16] = {0};
            int pos = -1, consumed = 0;
            if (sscanf(l.c_str(), "%15s %d %n", fr, &pos, &consumed) < 2) {
                fprintf(stderr, "%s:%d: expected '<ldu1|ldu2> <position> <11 hex bytes>'\n",
                        pairs_file.c_str(), lineno);
                fclose(pf); return 1;
            }
            std::string frs(fr);
            if (frs != "ldu1" && frs != "ldu2") {
                fprintf(stderr, "%s:%d: frame must be ldu1 or ldu2, got %s\n",
                        pairs_file.c_str(), lineno, fr);
                fclose(pf); return 1;
            }
            if (pos < 0 || pos > 8) {
                fprintf(stderr, "%s:%d: position must be 0..8, got %d\n",
                        pairs_file.c_str(), lineno, pos);
                fclose(pf); return 1;
            }
            std::vector<uint8_t> cv = hex_to_bytes(l.c_str() + consumed);
            if (cv.size() != 11) {
                fprintf(stderr, "%s:%d: ct must be 11 bytes (got %zu)\n",
                        pairs_file.c_str(), lineno, cv.size());
                fclose(pf); return 1;
            }
            cand_ct.insert(cand_ct.end(), cv.begin(), cv.end());
            cand_off.push_back(offset_of(frs, pos));
            cand_label.push_back(frs + " position " + std::to_string(pos));
        }
        fclose(pf);
        if (cand_ct.empty()) {
            fprintf(stderr, "no candidates in %s\n", pairs_file.c_str());
            return 1;
        }
    } else {
        cand_ct = ct_v;
        cand_off.push_back(offset_of(frame, position));
        cand_label.push_back(frame + " position " + std::to_string(position));
    }
    const int ncand = (int)cand_off.size();
    if (ncand == 1) {
        fprintf(stderr, "%s -> keystream offset %d\n",
                cand_label[0].c_str(), cand_off[0]);
    } else {
        fprintf(stderr, "%d candidate codeword(s) from %s, offsets %d..%d "
                        "(one keystream per key covers them all)\n",
                ncand, pairs_file.c_str(),
                *std::min_element(cand_off.begin(), cand_off.end()),
                *std::max_element(cand_off.begin(), cand_off.end()));
    }
    const uint64_t TOTAL = 1ULL << 40;
    if (start + count > TOTAL) count = TOTAL - start;
    if (start >= TOTAL) { fprintf(stderr, "--start out of range\n"); return 1; }

    // Upload inputs
    GPUState g;
    CUDA_CHECK(cudaMalloc(&g.d_mi, 8));
    CUDA_CHECK(cudaMalloc(&g.d_ct, cand_ct.size()));
    CUDA_CHECK(cudaMalloc(&g.d_pt, 11));
    CUDA_CHECK(cudaMalloc(&g.d_hits, sizeof(uint64_t)));
    CUDA_CHECK(cudaMalloc(&g.d_key, 5));
    int *d_off = nullptr, *d_cand = nullptr;
    CUDA_CHECK(cudaMalloc(&d_off, sizeof(int) * ncand));
    CUDA_CHECK(cudaMalloc(&d_cand, sizeof(int)));
    CUDA_CHECK(cudaMemcpy(d_off, cand_off.data(), sizeof(int) * ncand, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemset(d_cand, 0xFF, sizeof(int)));   // -1 = no candidate matched
    CUDA_CHECK(cudaStreamCreate(&g.stream));
    CUDA_CHECK(cudaMemcpy(g.d_mi, &mi_v[0], 8, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(g.d_ct, cand_ct.data(), cand_ct.size(), cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemcpy(g.d_pt, pt_v.data(), 11, cudaMemcpyHostToDevice));
    CUDA_CHECK(cudaMemset(g.d_hits, 0, sizeof(uint64_t)));

    int block = 1024;
    fprintf(stderr, "ADP/RC4 CUDA brute force: shard [%llu .. %llu) of %llu total, %d blocks x %d threads\n",
            (unsigned long long)start, (unsigned long long)(start + count), (unsigned long long)TOTAL, nblocks, block);

    // Optional found-file polling thread (mirrors CPU binary behavior).
    std::thread poller;
    std::atomic<bool> remote_stop(false);
    if (found_file_set) {
        poller = std::thread(poll_found_file, found_file, g.stream, std::ref(remote_stop));
    }
    // Optional progress reporter.
    std::thread progressor;
    std::atomic<bool> progress_stop(false);
    if (progress_on) {
        progressor = std::thread(report_progress, count, std::ref(progress_stop), throughput);
    }

    brute_kernel<<<nblocks, block, 0, g.stream>>>(
        g.d_mi, g.d_ct, g.d_pt, ncand, d_off, start, count,
        g.d_hits, g.d_key, d_cand);
    // Wait for the search kernel FIRST. The poller only returns when it sees a
    // remote key or is told to stop; joining it before the kernel finished would
    // hang forever whenever no other shard writes the found-file (i.e. the common
    // case, including the shard that wins locally). The poller still runs
    // concurrently with the kernel above, so a remote key found mid-run sets
    // g_stopFlag as before.
    CUDA_CHECK(cudaStreamSynchronize(g.stream));
    // Kernel done (found a key or exhausted the shard) -> release the helpers.
    remote_stop = true;                 // let the found-file poller exit its loop
    if (found_file_set && poller.joinable()) poller.join();
    progress_stop = true;
    if (progressor.joinable()) progressor.join();

    uint64_t hits = 0;
    CUDA_CHECK(cudaMemcpy(&hits, g.d_hits, sizeof(uint64_t), cudaMemcpyDeviceToHost));
    uint8_t key5[5] = {0};
    bool local_found = (hits != 0);
    if (local_found) {
        CUDA_CHECK(cudaMemcpy(key5, g.d_key, 5, cudaMemcpyDeviceToHost));
        int which = -1;
        CUDA_CHECK(cudaMemcpy(&which, d_cand, sizeof(int), cudaMemcpyDeviceToHost));
        if (which >= 0 && which < ncand) {
            // Which codeword's plaintext guess held. With a whole superframe in
            // flight the key alone does not say that, and it is the fact worth
            // keeping: it identifies a confirmed idle frame.
            fprintf(stdout, "MATCHED CANDIDATE: %s\n", cand_label[which].c_str());
        }
        fprintf(stdout, "KEY FOUND: ");
        for (int i = 0; i < 5; ++i) fprintf(stdout, "%02x ", key5[i]);
        fprintf(stdout, "\n");
        if (found_file_set) {
            FILE *f = fopen(found_file.c_str(), "wb");
            if (f) { fwrite(key5, 1, 5, f); fclose(f); }
        }
        // Print keystream prefix for cross-check (re-run KSA on CPU, cheap).
        // Reuse the same schedule as the CPU binary.
        uint8_t adp_key[13], S[256], K[256];
        for (int i = 0; i < 5; ++i) adp_key[i] = key5[i];
        for (int i = 5; i < 13; ++i) adp_key[i] = mi_v[i - 5]; // mi[0..7]
        // Note: mi_v has 9 bytes; the schedule uses the first 8 (i-5 in 5..12).
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
        // Cross-check around the codeword that actually matched, not around a
        // single global offset -- with a superframe in flight there isn't one.
        const int shown = (which >= 0 && which < ncand) ? cand_off[which] : cand_off[0];
        fprintf(stdout, "keystream[%d..%d]: ", shown, shown + 31);
        for (int k2 = 0; k2 < 32 && shown + k2 < 469; ++k2)
            fprintf(stdout, "%02x ", ks[shown + k2]);
        fprintf(stdout, "\n");
        // Cleanup
        cudaFree(g.d_mi); cudaFree(g.d_ct); cudaFree(g.d_pt);
        cudaFree(g.d_hits); cudaFree(g.d_key);
        cudaFree(d_off); cudaFree(d_cand);
        cudaStreamDestroy(g.stream);
        return 0;
    }

    // Remote found (another shard via shared file)
    if (g_remote_found) {
        fprintf(stdout, "KEY FOUND (remote shard): ");
        for (int i = 0; i < 5; ++i) fprintf(stdout, "%02x ", g_remote_found_key[i]);
        fprintf(stdout, "\n");
        cudaFree(g.d_mi); cudaFree(g.d_ct); cudaFree(g.d_pt);
        cudaFree(g.d_hits); cudaFree(g.d_key);
        cudaStreamDestroy(g.stream);
        return 0;
    }

    cudaFree(g.d_mi); cudaFree(g.d_ct); cudaFree(g.d_pt);
    cudaFree(g.d_hits); cudaFree(g.d_key);
    cudaStreamDestroy(g.stream);
    fprintf(stderr, "no key found in this shard [%llu .. %llu)\n",
            (unsigned long long)start, (unsigned long long)(start + count));
    return 2;
}
