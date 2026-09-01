#!/usr/bin/env python3
"""Watch recordings/ for new .wav files and transcribe them with local whisper.

Runs as a long-lived process alongside lwin_listen.sh / udp_audio_record.py.
When udp_audio_record.py saves a new TG*.wav, this watcher sees it within a
few seconds and transcribes it, writing a .txt next to the .wav and indexing
the transcript into sdr.db.

Transcription goes to the persistent CUDA whisper-server (scripts/stt_server.sh)
over HTTP, which is 8x the old spawn-whisper-cli-per-file path: 0.133 s/clip
against 1.081 s. See scripts/stt_backend.py for why the GPU only pays off behind
a long-lived process. If the server is down the CPU binary takes over, slowly.

Usage:
  stt_watch.py [--dir recordings] [--interval 3]

Idempotent: only transcribes files without an existing .txt. A file that
already has a .txt still gets its transcript re-indexed into sdr.db, because
the .txt is written before the database row is committed and a process killed
between the two would otherwise leave that transcript permanently unindexed.

Works newest-first, so live calls are transcribed ahead of any backlog.
"""
import argparse, collections, glob, os, signal, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stt_backend

R = '/home/besquivel/rtl'
DEFAULT_MODEL = stt_backend.DEFAULT_MODEL

# A transport failure is retryable, so a file that hits one is left unseen and
# picked up next pass. This caps that: a .wav that is genuinely undecodable would
# otherwise be retried every --interval seconds for the life of the process.
MAX_ATTEMPTS = 5

def merge_transcript(_unused, outdir, filename):
    """Store the transcript in sdr.db.

    Previously this read calls.json, edited it and wrote the whole file back.
    That raced udp_audio_record.py, which rewrote the same file wholesale at
    session end — so every transcript merged here was reliably clobbered a few
    minutes later, which is why calls.json never contained one. A row UPDATE
    cannot lose a concurrent writer's work.

    The .txt file next to the .wav remains the durable copy; this only indexes
    it for search — including when it is empty, which is how a clip whisper
    heard nothing in stops carrying an older model's transcript.
    """
    txt_path = os.path.join(outdir, os.path.splitext(filename)[0] + '.txt')
    if not os.path.exists(txt_path):
        return
    try:
        text = open(txt_path, errors='replace').read().strip()
    except OSError:
        return
    # An EMPTY .txt is stored as an empty transcript rather than skipped, so the
    # row always mirrors the .txt beside it — the same rule stt_transcribe.py's
    # bulk indexer follows. Skipping would leave a previous model's output in a
    # row whose durable copy on disk is now blank.

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
    p.add_argument('--url', default=stt_backend.DEFAULT_URL,
                   help='persistent whisper-server base URL')
    p.add_argument('--no-cpu-fallback', action='store_true',
                   help='wait for the server rather than transcribing on CPU')
    a = p.parse_args()
    os.makedirs(a.dir, exist_ok=True)
    seen = set()
    attempts: collections.Counter[str] = collections.Counter()
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
    print(f'stt_watch: model {os.path.basename(a.model)} via {a.url}', flush=True)
    if not stt_backend.server_ready(a.url):
        print(f'stt_watch: WARNING {a.url} not answering — '
              f'start it with scripts/stt_server.sh start', flush=True)
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
        # Probed at most once per sweep, and only if something actually fails:
        # when the server is down the probe pays a connect timeout, and doing
        # that per pending file would stall the whole sweep.
        server_up: bool | None = None
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
            name = os.path.basename(w)
            print(f'stt_watch: transcribing {name}', flush=True)
            outcome = stt_backend.transcribe(
                w, a.dir, url=a.url, model=a.model, lang=a.lang,
                threads=a.threads, allow_cpu=not a.no_cpu_fallback,
                log=lambda m: print(f'stt_watch: {m}', flush=True))
            if outcome == stt_backend.OK:
                attempts.pop(w, None)
                merge_transcript(os.path.join(a.dir, 'calls.json'), a.dir, name)
                print(f'stt_watch: done ({time.time()-t0:.1f}s)', flush=True)
                seen.add(w)
            elif outcome == stt_backend.RETRY:
                # Deliberately NOT added to `seen`. A refused connection while
                # the server restarts is routine, and the old code's
                # unconditional seen.add() would have dropped the file for the
                # life of the process.
                #
                # A whole-server outage does not count against any one file:
                # otherwise a 15-minute container restart would permanently
                # abandon the first MAX_ATTEMPTS files of the backlog, which is
                # precisely the "silently stopped transcribing" failure that
                # transcriber.ts was written to end.
                if server_up is None:
                    server_up = stt_backend.server_ready(a.url)
                if not server_up:
                    print(f'stt_watch: {a.url} down; not counting an attempt '
                          f'against {name}', flush=True)
                    continue
                attempts[w] += 1
                if attempts[w] >= MAX_ATTEMPTS:
                    print(f'stt_watch: giving up on {name} after '
                          f'{attempts[w]} attempts', flush=True)
                    seen.add(w)
                else:
                    print(f'stt_watch: will retry {name} '
                          f'({attempts[w]}/{MAX_ATTEMPTS})', flush=True)
            else:
                print(f'stt_watch: FAILED {name}', flush=True)
                seen.add(w)
        time.sleep(a.interval)
    print('stt_watch: stopped.')

if __name__ == '__main__':
    sys.exit(main())
