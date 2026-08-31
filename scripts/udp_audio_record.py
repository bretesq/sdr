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
GAP, MINDUR, TG_TTL = 2.0, 0.7, 12.0

os.makedirs(OUT, exist_ok=True)
try:    tgdb = json.load(open(DB))
except Exception: tgdb = {}

ANSI = re.compile(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][A-Z0-9]')
TGPAT = re.compile(r'voice update:\s+tg\((\d+)\)|hold active tg\((\d+)\)|set tgid=(\d+)')

def slug(s, n=28):
    s = re.sub(r'[^A-Za-z0-9]+', '-', (s or '').strip()).strip('-')
    return (s[:n].rstrip('-') or 'unknown')

# Per-call P25 metadata, all read from op25's own log output.
#
#   voice update:  tg(17169), freq(851287500), slot(-), prio(3)
#   ESS: algid=aa, keyid=8, mi=00 00 00 00 00 00 00 00 00
#   rfss_sts_bcst: syid: 1bd rfid: 1 stid: 13 ch1: 16e8(773.056250)
#
# ESS needs op25 at -v 10. Everything else appears at the default verbosity.
FREQPAT = re.compile(r'voice update:\s*tg\((\d+)\),\s*freq\((\d+)\)')
ESSPAT  = re.compile(r'ESS:\s*algid=([0-9a-f]+),\s*keyid=([0-9a-f]+),\s*mi=([0-9a-f ]{26})')
SITEPAT = re.compile(r'rfss_sts_bcst:\s*syid:\s*([0-9a-f]+)\s*rfid:\s*(\d+)\s*stid:\s*(\d+)')
NACPAT  = re.compile(r'NAC\s+0x([0-9a-f]{3})')


class LogTail:
    """Follow op25's log and expose the current call's metadata.

    Everything here is best-effort and time-bounded by TG_TTL: op25 emits these
    lines asynchronously from the audio stream, so a value older than the
    freshness window belongs to a previous call and must not be attached to
    this one. That is the same rule the talkgroup already used.
    """
    def __init__(self, path):
        self.path, self.fh, self.buf = path, None, ''
        self.tg, self.tg_t = None, 0.0
        self.freq, self.freq_t = None, 0.0
        self.ess, self.ess_t = None, 0.0          # (algid, keyid, mi)
        self.site = None                           # (sysid, rfss, stid) — static per site
        self.nac = None

    def poll(self):
        if self.fh is None:
            if not os.path.exists(self.path): return
            self.fh = open(self.path, 'r', errors='ignore')
        chunk = self.fh.read()
        if not chunk: return
        self.buf += ANSI.sub('', chunk)
        now = time.time()

        # op25 rewrites the status line without newlines, so scan the whole buffer tail
        for m in TGPAT.finditer(self.buf):
            tg = next(g for g in m.groups() if g)
            self.tg, self.tg_t = int(tg), now

        # tg+freq together: the grant's voice channel for this call
        for m in FREQPAT.finditer(self.buf):
            self.tg, self.tg_t = int(m.group(1)), now
            self.freq, self.freq_t = int(m.group(2)), now

        # ESS is the AUTHORITATIVE encryption signal for this specific call,
        # independent of the reference DB's static enc flag — which is known to
        # disagree: TG 17086 is flagged 'full' in RadioReference but transmitted
        # algid 0x80 (clear) in all 23 observations here.
        for m in ESSPAT.finditer(self.buf):
            self.ess = (int(m.group(1), 16), int(m.group(2), 16),
                        m.group(3).replace(' ', ''))
            self.ess_t = now

        for m in SITEPAT.finditer(self.buf):
            self.site = (int(m.group(1), 16), int(m.group(2)), int(m.group(3)))

        for m in NACPAT.finditer(self.buf):
            self.nac = int(m.group(1), 16)

        self.buf = self.buf[-8000:]

    def current(self):
        if self.tg is not None and time.time() - self.tg_t < TG_TTL:
            return self.tg
        return None

    def metadata(self):
        """Fresh per-call metadata. Stale values are dropped, not guessed."""
        now = time.time()
        algid = keyid = mi = None
        if self.ess and now - self.ess_t < TG_TTL:
            algid, keyid, mi = self.ess
        return {
            'freq':  self.freq if (self.freq and now - self.freq_t < TG_TTL) else None,
            'algid': algid, 'keyid': keyid, 'mi': mi,
            'sysid': self.site[0] if self.site else None,
            'rfss':  self.site[1] if self.site else None,
            'site':  self.site[2] if self.site else None,
            'nac':   self.nac,
        }

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
                                   ended_at=call['t'], **call.get('meta', {}))
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
