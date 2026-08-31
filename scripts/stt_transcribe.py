#!/usr/bin/env python3
"""Transcribe LWIN recordings with local whisper.cpp (CPU).

Usage:
  stt_transcribe.py [--dir recordings] [--out transcripts] [--model MODEL] [--resume]

Reads each .wav in --dir, runs whisper-cli, and writes <name>.txt into --out.
Also rewrites the corresponding calls.json entry with a 'transcript' field.
Idempotent: files that already have a .txt are skipped unless --resume is not set...
actually: by default we skip existing .txt (use --force to re-run).
"""
import argparse, glob, json, os, subprocess, sys, time

R = '/home/besquivel/rtl'
DEFAULT_BIN = f'{R}/tools/whisper.cpp/build/bin/whisper-cli'
DEFAULT_MODEL = f'{R}/models/ggml-small.en.bin'
LDLIBS = f'{R}/tools/whisper.cpp/build/bin'

def transcribe(wav, model, outdir, force, threads=8, lang='en', log=None):
    base = os.path.splitext(os.path.basename(wav))[0]
    out = os.path.join(outdir, base + '.txt')
    if not force and os.path.exists(out):
        return None
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = os.pathsep.join([os.path.dirname(DEFAULT_BIN), LDLIBS])
    cmd = [DEFAULT_BIN, '-m', model, '-f', wav, '-l', lang,
           '--threads', str(threads),
           '--output-txt', '--output-file', os.path.join(outdir, base)]
    if log:
        with open(log, 'a') as f:
            f.write(f'\n=== {os.path.basename(wav)}\n')
            r = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            f.write(r.stdout.decode('utf-8', 'replace'))
            f.flush()
            return r.returncode == 0
    else:
        r = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return r.returncode == 0

def merge_transcripts(_unused, outdir):
    """Index every .txt in `outdir` into sdr.db.

    Was a read-modify-write of calls.json, which udp_audio_record.py rewrote
    wholesale at session end — so merged transcripts were routinely destroyed.
    Row updates do not race a concurrent writer. The .txt files remain the
    durable copy; this only makes them searchable.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import sdr_db
    except ImportError as e:
        print(f'cannot index transcripts: {e}')
        return 0

    db = sdr_db.connect()
    n = 0
    try:
        for txt in sorted(glob.glob(os.path.join(outdir, 'TG*.txt'))):
            try:
                text = open(txt, errors='replace').read().strip()
            except OSError:
                continue
            if not text:
                continue
            sdr_db.set_transcript(db, os.path.basename(txt)[:-4] + '.wav', text)
            n += 1
        db.commit()
    finally:
        db.close()
    return n

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dir', default=f'{R}/recordings')
    p.add_argument('--out', default=f'{R}/recordings')
    p.add_argument('--model', default=DEFAULT_MODEL)
    p.add_argument('--force', action='store_true', help='re-transcribe files with existing .txt')
    p.add_argument('--threads', type=int, default=8)
    p.add_argument('--lang', default='en')
    p.add_argument('--no-merge', action='store_true', help='do not index transcripts in sdr.db')
    p.add_argument('--log', default=None, help='append whisper output to this file')
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)
    wavs = sorted(glob.glob(os.path.join(a.dir, 'TG*.wav')))
    if not wavs:
        print(f'no .wav files in {a.dir}'); return 0
    t0 = time.time()
    ok, skip, fail = 0, 0, 0
    for w in wavs:
        r = transcribe(w, a.model, a.out, a.force, a.threads, a.lang, a.log)
        if r is None: skip += 1
        elif r: ok += 1
        else: fail += 1
    print(f'{len(wavs)} files: {ok} transcribed, {skip} skipped, {fail} failed '
          f'({time.time()-t0:.1f}s)')
    if not a.no_merge:
        n = merge_transcripts(None, a.out)
        print(f'{n} transcript(s) indexed in sdr.db')
    return 0

if __name__ == '__main__':
    sys.exit(main())
