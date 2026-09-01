#!/usr/bin/env python3
"""Batch-transcribe LWIN recordings via the persistent whisper-server.

Usage:
  stt_transcribe.py [--dir recordings] [--out transcripts] [--model MODEL] [--force]

Reads each .wav in --dir, POSTs it to the whisper-server (scripts/stt_server.sh,
CPU whisper-cli as fallback), and writes <name>.txt into --out.
Also rewrites the corresponding calls.json entry with a 'transcript' field.
Idempotent: files that already have a .txt are skipped unless --resume is not set...
actually: by default we skip existing .txt (use --force to re-run).
"""
import argparse, glob, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stt_backend

R = '/home/besquivel/rtl'
DEFAULT_MODEL = stt_backend.DEFAULT_MODEL

def transcribe(wav, model, outdir, force, threads=8, lang='en', log=None,
               url=stt_backend.DEFAULT_URL, allow_cpu=True):
    """Returns None if skipped, True on success, False on failure."""
    base = os.path.splitext(os.path.basename(wav))[0]
    out = os.path.join(outdir, base + '.txt')
    if not force and os.path.exists(out):
        return None

    def _log(m):
        line = f'{os.path.basename(wav)}: {m}'
        print(line, flush=True)
        if log:
            with open(log, 'a') as f:
                f.write(line + '\n')

    outcome = stt_backend.transcribe(wav, outdir, url=url, model=model, lang=lang,
                                     threads=threads, allow_cpu=allow_cpu, log=_log)
    return outcome == stt_backend.OK

def merge_transcripts(_unused, outdir):
    """Make sdr.db mirror every .txt in `outdir`.

    Was a read-modify-write of calls.json, which udp_audio_record.py rewrote
    wholesale at session end — so merged transcripts were routinely destroyed.
    Row updates do not race a concurrent writer. The .txt files remain the
    durable copy; this only makes them searchable.

    An EMPTY .txt clears the stored transcript rather than being skipped. This
    matters on a model swap: medium.en hears silence on a few clips where
    small.en emitted text, and skipping those would leave the row holding the
    old model's output while the .txt beside it — the documented durable copy —
    is empty. set_transcript('') also drops that call's rows from call_codes,
    so no 10-code mention outlives the transcript it was extracted from.
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
    p.add_argument('--log', default=None, help='append transport messages to this file')
    p.add_argument('--url', default=stt_backend.DEFAULT_URL,
                   help='persistent whisper-server base URL')
    p.add_argument('--no-cpu-fallback', action='store_true',
                   help='fail rather than transcribing on CPU')
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)
    wavs = sorted(glob.glob(os.path.join(a.dir, 'TG*.wav')))
    if not wavs:
        print(f'no .wav files in {a.dir}'); return 0
    t0 = time.time()
    ok, skip, fail = 0, 0, 0
    for w in wavs:
        r = transcribe(w, a.model, a.out, a.force, a.threads, a.lang, a.log,
                       url=a.url, allow_cpu=not a.no_cpu_fallback)
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
