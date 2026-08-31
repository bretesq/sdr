#!/usr/bin/env python3
"""Record op25's UDP audio to per-call WAVs, named with the talkgroup at save time.

op25 emits 320-byte UDP packets = 160 samples of S16LE PCM @ 8 kHz (2-byte packets are
control flags). The audio stream carries no talkgroup ID, so we tail op25's log in
parallel and track the currently-active talkgroup.

IMPORTANT: run op25 with `python3 -u` or its stdout is block-buffered and the log lags,
which mislabels calls.

Usage: udp_audio_record.py [port] [seconds] [outdir] [op25_log]
"""
import socket, sys, wave, time, os, select, json, re, datetime, signal

# The log parser lives in its own module so it can be imported and tested;
# this file executes at import time and therefore cannot be (see
# scripts/tests/test_static.py). It also handles BOTH op25 trunking log
# formats -- rx.py's and multi_rx.py's differ -- and per-receiver filtering.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from op25_log import LogTail, TG_TTL          # noqa: E402  (path set above)

# The main loop is blocked most of its time in select(); a SIGINT arriving
# there used to leave the process alive (Python only delivers the default
# KeyboardInterrupt handler when executing bytecode). Exit explicitly on
# SIGINT / SIGTERM so the web /stop endpoint actually stops the session.
def _on_signal(signum, frame):
    raise SystemExit(0)
signal.signal(signal.SIGINT, _on_signal)
signal.signal(signal.SIGTERM, _on_signal)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 23456
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 400
OUT  = sys.argv[3] if len(sys.argv) > 3 else '/home/besquivel/rtl/recordings'
LOG  = sys.argv[4] if len(sys.argv) > 4 else '/home/besquivel/rtl/results/op25_record.log'
DB   = '/home/besquivel/rtl/reference/lwin_talkgroups.json'
GAP, MINDUR = 2.0, 0.7

os.makedirs(OUT, exist_ok=True)
try:    tgdb = json.load(open(DB))
except Exception: tgdb = {}

def slug(s, n=28):
    s = re.sub(r'[^A-Za-z0-9]+', '-', (s or '').strip()).strip('-')
    return (s[:n].rstrip('-') or 'unknown')

socks = []
for p in (PORT, PORT + 1):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    s.bind(('127.0.0.1', p)); s.setblocking(False); socks.append(s)
tail = LogTail(LOG)
print(f"listening 127.0.0.1:{PORT}/{PORT+1} for {SECS:.0f}s -> {OUT}/", flush=True)
print(f"talkgroup source: {LOG}", flush=True)

# The database is opened best-effort. The .wav is the irreplaceable artifact;
# metadata is derivable from the filename and the WAV header, so a database
# problem must never abort a recording. Failures are loud, not silent.
db = None
# Set by the web console when it spawns lwin_listen.sh, inherited through bash.
# Absent when the script is run by hand from a terminal, which is fine: the call
# is still recorded, just not attributed to a console session.
try:
    SESSION_ID = int(os.environ['SDR_SESSION_ID'])
except (KeyError, ValueError):
    SESSION_ID = None
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sdr_db
    db = sdr_db.connect()
except Exception as e:                                    # noqa: BLE001
    print(f"WARNING: no database ({e}); metadata will need "
          f"`python3 scripts/import_to_sqlite.py` to backfill", flush=True)

buf, call, calls, pkts = bytearray(), None, [], 0
t0 = time.time()

def flush():
    global buf, call
    if call is None or not buf:
        buf = bytearray(); call = None; return
    dur = len(buf) / 2 / 8000.0
    if dur >= MINDUR:
        tg = call['tg']
        e = tgdb.get(str(tg), {}) if tg else {}
        stamp = datetime.datetime.fromtimestamp(call['start']).strftime('%Y%m%d-%H%M%S')
        name = f"TG{tg}_{slug(e.get('alpha'))}_{stamp}.wav" if tg else f"TGunknown_{stamp}.wav"
        path = os.path.join(OUT, name)
        i = 2
        while os.path.exists(path):
            path = os.path.join(OUT, name[:-4] + f"_{i}.wav"); i += 1
        w = wave.open(path, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(bytes(buf)); w.close()
        fname = os.path.basename(path)
        calls.append({'file': fname, 'tgid': tg, 'alpha': e.get('alpha'),
                      'desc': e.get('desc'), 'enc': e.get('enc'),
                      'cat': e.get('cat'), 'start': call['start'],
                      'dur': round(dur, 2)})
        # Committed per call, not accumulated for a single write at exit. A
        # call is durable the moment it is flushed, so a crash or a SIGKILL
        # loses at most the one in progress.
        if db is not None:
            try:
                sdr_db.upsert_call(db, file=fname, tgid=tg,
                                   start=call['start'], dur=round(dur, 2),
                                   ended_at=call['t'], session_id=SESSION_ID,
                                   **call.get('meta', {}))
                db.commit()
            except Exception as e2:                        # noqa: BLE001
                print(f"  WARNING: could not record {fname} in the database: {e2}",
                      flush=True)
        print(f"  {os.path.basename(path)}  {dur:.1f}s  {e.get('desc','(unknown TG)')[:44]}", flush=True)
    buf = bytearray(); call = None

try:
    while time.time() - t0 < SECS:
        tail.poll()
        r, _, _ = select.select(socks, [], [], 0.2)
        now = time.time()
        for s in r:
            while True:
                try: d, _ = s.recvfrom(65535)
                except BlockingIOError: break
                pkts += 1
                if len(d) >= 320:
                    if call is None:
                        call = {'start': now, 'tg': tail.current(), 't': now,
                                'meta': tail.metadata()}
                    elif call['tg'] is None:
                        call['tg'] = tail.current()      # grant may land just after audio
                        # The ESS and the voice-channel grant can arrive after
                        # the first audio packet too; take them while the call
                        # is still open rather than losing them.
                        fresh = tail.metadata()
                        for k, v in fresh.items():
                            if call['meta'].get(k) is None and v is not None:
                                call['meta'][k] = v
                    buf += d; call['t'] = now
        if call and time.time() - call['t'] > GAP:
            flush()
finally:
    flush()
    # NO json.dump of calls.json here.
    #
    # That line was a truncating write of ONLY this session's calls, so every
    # run discarded the metadata for every recording that came before it. A
    # 60-second session took the file from 2,953 entries to 7. It also
    # clobbered the transcripts stt_watch.py had merged in, which is why none
    # ever survived. Calls are now committed to sdr.db as they are flushed.
    if db is not None:
        db.close()
named = sum(1 for c in calls if c['tgid'])
print(f"\nUDP packets: {pkts}", flush=True)
print(f"{len(calls)} call(s), {sum(c['dur'] for c in calls):.1f}s audio, {named} labelled with talkgroup", flush=True)
