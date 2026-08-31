// imbe_encode.cpp - encode a 16-bit 8 kHz WAV to 11-byte IMBE codewords.
// Usage: imbe_encode <wav> [frame_index]
// Prints one 11-byte codeword (160-sample frame) so it can be used as a real
// known-plaintext for the ADP/RC4 key recovery (pairing with the CIPHERTXT
// ciphertext + MI from the op25 log).
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "imbe_vocoder/imbe_vocoder.h"
#include "op25_imbe_frame.h"

typedef signed short int16_t;

// Read a 16-bit mono WAV into a vector<int16_t>.
// Robust approach: read the whole file, locate the "data" chunk marker,
// and read the PCM samples (16-bit, mono).
static bool read_wav(const char *path, std::vector<int16_t> &out) {
    FILE *f = fopen(path, "rb");
    if (!f) return false;
    // Read the entire file.
    std::vector<char> buf;
    {
        fseek(f, 0, SEEK_END);
        long sz = ftell(f);
        fseek(f, 0, SEEK_SET);
        if (sz <= 0) { fclose(f); return false; }
        buf.resize(sz);
        if (fread(buf.data(), 1, sz, f) != (size_t)sz) { fclose(f); return false; }
    }
    fclose(f);
    // Find the "data" chunk: the 4 bytes "data" followed by a 4-byte LE size, then PCM.
    const size_t B = buf.size();
    for (size_t i = 0; i + 8 <= B; ++i) {
        if (buf[i]=='d' && buf[i+1]=='a' && buf[i+2]=='t' && buf[i+3]=='a') {
            // Verify it's a chunk header: the preceding chunk-name field should be a known
            // WAV chunk. We accept the first "data" marker that is preceded by a valid
            // chunk structure (offset 12-aligned in a normal WAV).
            // Read the 4-byte little-endian chunk size.
            unsigned int csz = (unsigned int)(
                (uint32_t)(unsigned char)buf[i+4]
                | ((uint32_t)(unsigned char)buf[i+5] << 8)
                | ((uint32_t)(unsigned char)buf[i+6] << 16)
                | ((uint32_t)(unsigned char)buf[i+7] << 24));
            size_t pcm_off = i + 8;
            if (pcm_off + (size_t)csz > B) return false;
            if (csz % 2 != 0) csz &= ~1u;
            out.resize(csz / 2);
            // Reinterpret 16-bit LE PCM as int16_t.
            const unsigned char *p = (const unsigned char *)buf.data() + pcm_off;
            for (size_t s = 0; s < out.size(); ++s) {
                int16_t v = (int16_t)(
                    (unsigned char)p[s*2] | ((unsigned char)p[s*2+1] << 8));
                out[s] = v;
            }
            return true;
        }
    }
    return false;
}

int main(int argc, char **argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <wav> [frame_index | seq <start> <count>]\n", argv[0]);
        return 1;
    }
    // seq mode: encode `count` consecutive frames from `start` in ONE process,
    // so the IMBE vocoder's persistent state (pitch, spectral amplitudes, etc.)
    // carries across frames. This mirrors how op25 processes audio.
    if (argc >= 4 && !strcmp(argv[2], "seq")) {
        if (argc < 5) {
            fprintf(stderr, "usage: %s <wav> seq <start> <count>\n", argv[0]);
            return 1;
        }
        int start = atoi(argv[3]);
        int count = atoi(argv[4]);
        std::vector<int16_t> samples;
        if (!read_wav(argv[1], samples)) {
            fprintf(stderr, "failed to read wav: %s\n", argv[1]);
            return 2;
        }
        imbe_vocoder vocoder;
        for (int i = 0; i < count; ++i) {
            int fnum = start + i;
            const int SND_FRAME = 160;  // p25p1_fdma::SND_FRAME (pcm samples per frame)
            size_t off = (size_t)fnum * SND_FRAME;
            if (off + SND_FRAME > samples.size()) break;
            uint32_t u[8];
            vocoder.imbe_encode((int16_t *)u, (int16_t *)&samples[off]);
            packed_codeword cw;
            imbe_pack(cw, u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7]);
            printf("frame %d: %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x %02x\n",
                   fnum, cw[0], cw[1], cw[2], cw[3], cw[4],
                   cw[5], cw[6], cw[7], cw[8], cw[9], cw[10]);
        }
        return 0;
    }
    std::vector<int16_t> samples;
    if (!read_wav(argv[1], samples)) {
        fprintf(stderr, "failed to read wav: %s\n", argv[1]);
        return 2;
    }
    size_t nframes = samples.size() / 160;
    if (nframes == 0) {
        fprintf(stderr, "wav too short (%zu samples)\n", samples.size());
        return 2;
    }
    size_t frame_idx = (argc > 2) ? (size_t)atoi(argv[2]) : 0;
    if (frame_idx >= nframes) frame_idx = 0;

    imbe_vocoder vocoder;
    uint32_t u[8] = {0};
    // imbe_encode fills u[] (the 8 IMBE subwords) from a 160-sample frame.
    // The function signature is imbe_encode(int16_t *frame_vector, int16_t *snd);
    // frame_vector is an int16 array of 8 (the 8 IMBE subwords).
    int16_t fv[8];
    int16_t *snd = &samples[frame_idx * 160];
    vocoder.imbe_encode(fv, snd);
    for (int i = 0; i < 8; ++i) u[i] = (uint32_t)(uint16_t)fv[i];

    packed_codeword cw;
    imbe_pack(cw, u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7]);
    if (cw.size() != 11) {
        fprintf(stderr, "unexpected codeword size %zu\n", cw.size());
        return 3;
    }
    fprintf(stdout, "frame %zu of %zu:\n", frame_idx + 1, nframes);
    for (size_t i = 0; i < 11; ++i) fprintf(stdout, "%02x ", cw[i]);
    fprintf(stdout, "\n");
    return 0;
}
