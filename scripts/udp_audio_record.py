#!/usr/bin/env python3
"""Record op25's UDP audio stream to per-call WAV files (no sound card required).

op25 emits 320-byte UDP packets = 160 samples of S16LE PCM @ 8 kHz (2-byte packets
are control flags). A gap longer than GAP seconds ends the current call.
"""
import socket, wave, time, os, sys, select, json

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 23456
SECS = float(sys.argv[2]) if len(sys.argv) > 2 else 400
OUT  = sys.argv[3] if len(sys.argv) > 3 else '/home/besquivel/rtl/recordings'
GAP  = 2.0
MINDUR = 0.7

os.makedirs(OUT, exist_ok=True)
socks = []
for p in (PORT, PORT + 1):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    s.bind(('127.0.0.1', p)); s.setblocking(False); socks.append(s)
print(f"listening 127.0.0.1:{PORT}/{PORT+1} for {SECS:.0f}s -> {OUT}/", flush=True)

buf = bytearray(); last = None; calls = []; t0 = time.time(); pkts = 0
def flush():
    global buf, last
    if last is None or not buf: buf = bytearray(); return
    dur = len(buf) / 2 / 8000.0
    if dur >= MINDUR:
        fn = os.path.join(OUT, f"call-{last['start']:.3f}.wav")
        w = wave.open(fn, 'wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
        w.writeframes(bytes(buf)); w.close()
        calls.append({'file': fn, 'start': last['start'], 'dur': round(dur, 2)})
        print(f"  wrote {os.path.basename(fn)}  {dur:.1f}s", flush=True)
    buf = bytearray()

while time.time() - t0 < SECS:
    r, _, _ = select.select(socks, [], [], 0.3)
    now = time.time()
    for s in r:
        while True:
            try: d, _ = s.recvfrom(65535)
            except BlockingIOError: break
            pkts += 1
            if len(d) >= 320:
                if last is None: last = {'start': now}
                buf += d; last['t'] = now
    if last and 't' in last and time.time() - last['t'] > GAP:
        flush(); last = None
flush()
json.dump(calls, open(os.path.join(OUT, 'calls.json'), 'w'), indent=1)
print(f"\nUDP packets seen: {pkts}", flush=True)
print(f"{len(calls)} call(s) captured, {sum(c['dur'] for c in calls):.1f}s audio", flush=True)
