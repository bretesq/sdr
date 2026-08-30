#!/usr/bin/env python3
"""Record op25's UDP audio to per-call WAVs, named with the talkgroup at save time.

op25 emits 320-byte UDP packets = 160 samples of S16LE PCM @ 8 kHz (2-byte packets are
control flags). The audio stream carries no talkgroup ID, so we tail op25's log in
parallel and track the currently-active talkgroup.

IMPORTANT: run op25 with `python3 -u` or its stdout is block-buffered and the log lags,
which mislabels calls.

Usage: udp_audio_record.py [port] [seconds] [outdir] [op25_log]
"""
import socket, wave, time, os, sys, select, json, re, datetime

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

class LogTail:
    """Follow op25's log and expose the most recently active talkgroup."""
    def __init__(self, path):
        self.path, self.fh, self.buf = path, None, ''
        self.tg, self.tg_t = None, 0.0
    def poll(self):
        if self.fh is None:
            if not os.path.exists(self.path): return
            self.fh = open(self.path, 'r', errors='ignore')
        chunk = self.fh.read()
        if not chunk: return
        self.buf += ANSI.sub('', chunk)
        # op25 rewrites the status line without newlines, so scan the whole buffer tail
        for m in TGPAT.finditer(self.buf):
            tg = next(g for g in m.groups() if g)
            self.tg, self.tg_t = int(tg), time.time()
        self.buf = self.buf[-4000:]
    def current(self):
        if self.tg is not None and time.time() - self.tg_t < TG_TTL:
            return self.tg
        return None

socks = []
for p in (PORT, PORT + 1):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    s.bind(('127.0.0.1', p)); s.setblocking(False); socks.append(s)
tail = LogTail(LOG)
print(f"listening 127.0.0.1:{PORT}/{PORT+1} for {SECS:.0f}s -> {OUT}/", flush=True)
print(f"talkgroup source: {LOG}", flush=True)

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
        calls.append({'file': os.path.basename(path), 'tgid': tg,
                      'alpha': e.get('alpha'), 'desc': e.get('desc'),
                      'enc': e.get('enc'), 'cat': e.get('cat'),
                      'start': call['start'], 'dur': round(dur, 2)})
        print(f"  {os.path.basename(path)}  {dur:.1f}s  {e.get('desc','(unknown TG)')[:44]}", flush=True)
    buf = bytearray(); call = None

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
                    call = {'start': now, 'tg': tail.current(), 't': now}
                elif call['tg'] is None:
                    call['tg'] = tail.current()      # grant may land just after audio
                buf += d; call['t'] = now
    if call and time.time() - call['t'] > GAP:
        flush()
flush()
json.dump(calls, open(os.path.join(OUT, 'calls.json'), 'w'), indent=1)
named = sum(1 for c in calls if c['tgid'])
print(f"\nUDP packets: {pkts}", flush=True)
print(f"{len(calls)} call(s), {sum(c['dur'] for c in calls):.1f}s audio, {named} labelled with talkgroup", flush=True)
