#!/usr/bin/env python3
"""Watch recordings/ for new .wav files and transcribe them with local whisper.

Runs as a long-lived process alongside lwin_listen.sh / udp_audio_record.py.
When udp_audio_record.py saves a new TG*.wav, this watcher sees it within a
few seconds and runs whisper-cli on it, writing a .txt next to the .wav and
merging the transcript into calls.json.

Usage:
  stt_watch.py [--dir recordings] [--interval 3]

Idempotent: only transcribes files without an existing .txt. A file that
already has a .txt still gets its transcript re-indexed into sdr.db, because
the .txt is written before the database row is committed and a process killed
between the two would otherwise leave that transcript permanently unindexed.

Works newest-first, so live calls are transcribed ahead of any backlog.
"""
import argparse, glob, json, os, signal, subprocess, sys, time

R = '/home/besquivel/rtl'
DEFAULT_BIN = f'{R}/tools/whisper.cpp/build/bin/whisper-cli'
DEFAULT_MODEL = f'{R}/models/ggml-small.en.bin'
LDLIBS = f'{R}/tools/whisper.cpp/build/bin'

def run_whisper(wav, model, outdir, threads, lang='en'):
    base = os.path.splitext(os.path.basename(wav))[0]
    out = os.path.join(outdir, base + '.txt')
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = os.pathsep.join([os.path.dirname(DEFAULT_BIN), LDLIBS])
    base = os.path.splitext(os.path.basename(wav))[0]
    cmd = [DEFAULT_BIN, '-m', model, '-f', wav, '-l', lang,
           '--threads', str(threads),
           '--output-txt', '--output-file', os.path.join(outdir, base)]
    r = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return r.returncode == 0

def merge_transcript(_unused, outdir, filename):
    """Store the transcript in sdr.db.

    Previously this read calls.json, edited it and wrote the whole file back.
    That raced udp_audio_record.py, which rewrote the same file wholesale at
    session end — so every transcript merged here was reliably clobbered a few
    minutes later, which is why calls.json never contained one. A row UPDATE
    cannot lose a concurrent writer's work.

    The .txt file next to the .wav remains the durable copy; this only indexes
    it for search.
    """
    txt_path = os.path.join(outdir, os.path.splitext(filename)[0] + '.txt')
    if not os.path.exists(txt_path):
        return
    try:
        text = open(txt_path, errors='replace').read().strip()
    except OSError:
        return
    if not text:
        return

    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import sdr_db
        db = sdr_db.connect()
        try:
            sdr_db.set_transcript(db, filename, text)
            db.commit()
        finally:
            db.close()
    except Exception as e:                                 # noqa: BLE001
        # The .txt is written either way; only the index is missing, and
        # import_to_sqlite.py backfills it.
        print(f'stt_watch: could not index {filename}: {e}', flush=True)

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--dir', default=f'{R}/recordings')
    p.add_argument('--model', default=DEFAULT_MODEL)
    p.add_argument('--threads', type=int, default=8)
    p.add_argument('--interval', type=int, default=3)
    p.add_argument('--lang', default='en')
    a = p.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    seen = set()
    stop = {'flag': False}
    def _sig(_s, _f): stop['flag'] = True
    signal.signal(signal.SIGINT, _sig); signal.signal(signal.SIGTERM, _sig)

    # Which transcripts the database already has, read once. None means the
    # lookup failed, in which case re-indexing is skipped entirely rather than
    # blindly rewriting every row.
    indexed: set[str] | None = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import sdr_db
        _db = sdr_db.connect()
        try:
            indexed = {r['file'] for r in _db.execute(
                'SELECT file FROM calls WHERE transcript IS NOT NULL')}
        finally:
            _db.close()
    except Exception as e:                                 # noqa: BLE001
        print(f'stt_watch: cannot read indexed transcripts ({e}); '
              f'will not re-index orphaned .txt files', flush=True)

    print(f'stt_watch: tailing {a.dir} for new .wav (every {a.interval}s)', flush=True)
    if indexed is not None:
        print(f'stt_watch: {len(indexed)} transcripts already indexed', flush=True)
    while not stop['flag']:
        # NEWEST FIRST, by mtime.
        #
        # This was `sorted(glob.glob(...))`, i.e. by filename — and the names are
        # TG<id>_<alpha>_<timestamp>.wav, so that sorts by TALKGROUP ID, not by
        # time. With any backlog, whisper ground through low-numbered talkgroups
        # while the calls just recorded sat at the bottom of the list. Observed:
        # +174 transcripts against +34 new calls in one session, almost all of it
        # backfill, with the operator watching recent rows stay empty.
        #
        # Live calls now win; the backlog drains behind them.
        wavs = glob.glob(os.path.join(a.dir, 'TG*.wav'))
        for w in sorted(wavs, key=lambda p: os.path.getmtime(p), reverse=True):
            if w in seen: continue
            base = os.path.splitext(os.path.basename(w))[0]
            txt = os.path.join(a.dir, base + '.txt')
            if os.path.exists(txt):
                # A .txt with no DB row is possible: the .txt is written first
                # and the row committed after, so a process killed between the
                # two leaves that transcript unindexed forever — this skip means
                # the file is never revisited. Re-index just those, rather than
                # requiring a separate import_to_sqlite.py run.
                #
                # Gated on `indexed` so this costs one query at startup instead
                # of one connection per .txt on disk: 3,644 files here, of which
                # 12 were actually missing from the database.
                name = os.path.basename(w)
                if indexed is not None and name not in indexed:
                    merge_transcript(os.path.join(a.dir, 'calls.json'), a.dir, name)
                    indexed.add(name)
                seen.add(w); continue
            t0 = time.time()
            print(f'stt_watch: transcribing {os.path.basename(w)}', flush=True)
            ok = run_whisper(w, a.model, a.dir, a.threads, a.lang)
            if ok:
                merge_transcript(os.path.join(a.dir, 'calls.json'), a.dir, os.path.basename(w))
                print(f'stt_watch: done ({time.time()-t0:.1f}s)', flush=True)
            else:
                print(f'stt_watch: FAILED {os.path.basename(w)}', flush=True)
            seen.add(w)
        time.sleep(a.interval)
    print('stt_watch: stopped.')

if __name__ == '__main__':
    sys.exit(main())
