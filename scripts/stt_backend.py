#!/usr/bin/env python3
"""Speech-to-text transport: persistent whisper-server first, CPU whisper-cli as fallback.

WHY THIS MODULE EXISTS
----------------------
stt_watch.py and stt_transcribe.py both used to exec whisper-cli once per .wav.
On this corpus (median clip 2.5 s) that is dominated by fixed per-process cost,
and on the GPU it is dominated by uploading model weights to VRAM — ~690 ms of a
975 ms total, which is why spawn-per-clip on the GPU measured no faster than CPU.
Both callers now POST to one long-lived server (see stt_server.sh), which is
where the 8x actually lives.

THE .txt IS THE CONTRACT
------------------------
stt_watch.py treats "a .txt exists" as "this file is done" — that is its only
idempotency sentinel. whisper-cli --output-txt wrote one even when whisper heard
nothing, so silent clips were transcribed once and then skipped forever. This
module preserves that exactly: write_transcript() always writes the file, empty
transcript included. Omitting it would make the watcher re-POST every silent
clip on every pass, forever.

Writes are atomic (temp + os.replace) for the same reason: a half-written .txt
whose existence means "done" would permanently truncate a transcript.
"""
from __future__ import annotations

import mimetypes
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid

R = os.environ.get('SDR_ROOT', '/home/besquivel/rtl')

DEFAULT_URL = os.environ.get('STT_URL', 'http://127.0.0.1:8081')
# medium.en, not small.en: over 599 real clips it recovered 10-codes in 39 clips
# against small.en's 29, at an identical silence-confabulation rate (8/599).
# large-v3-turbo scored higher still (45) but confabulated on 56/599 — junk that
# would flow straight into call_codes and the FTS index — so it was rejected.
DEFAULT_MODEL = os.environ.get('STT_MODEL', f'{R}/models/ggml-medium.en.bin')
CPU_BIN = f'{R}/tools/whisper.cpp/build/bin/whisper-cli'

# Outcomes. RETRY exists because the failure modes of an HTTP call are not those
# of a subprocess: a connection refused during a server restart is routine and
# the file must be attempted again, whereas an unreadable .wav never will be.
OK, RETRY, FAIL = 'ok', 'retry', 'fail'


class TransportError(Exception):
    """The server could not be reached or did not answer. Retryable."""


def transcript_text(body: str) -> str:
    """Normalise a whisper text response to the exact bytes whisper-cli wrote.

    whisper-server returns one segment per line, each with the leading space
    that whisper's tokens carry (" Turn.\\n"); whisper-cli --output-txt strips it
    ("turn.\\n"). Matching byte-for-byte matters because 4,444 existing rows were
    produced by the old path and both feed the same tencodes extractor.

    Returns '' for silence — the caller still writes the file.
    """
    lines = [ln.strip() for ln in body.splitlines()]
    lines = [ln for ln in lines if ln]
    return ''.join(ln + '\n' for ln in lines)


def write_transcript(path: str, text: str) -> None:
    """Write a .txt atomically, creating it even when `text` is empty.

    Same directory for the temp file so os.replace stays a rename, not a copy
    across filesystems (which would not be atomic).
    """
    d = os.path.dirname(path) or '.'
    fd, tmp = tempfile.mkstemp(dir=d, prefix='.stt-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        # Leaving a .stt-*.tmp behind would be harmless, but leaving it as the
        # real .txt would mark a failed file "done".
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _multipart(wav: str, lang: str) -> tuple[bytes, str]:
    """Encode the inference request. stdlib only, so scripts/ stays dependency-free."""
    boundary = f'----rtl{uuid.uuid4().hex}'
    ctype = mimetypes.guess_type(wav)[0] or 'audio/wav'
    with open(wav, 'rb') as f:
        audio = f.read()

    parts: list[bytes] = []

    def field(name: str, value: str) -> None:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f'{value}\r\n'.encode())

    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        f'filename="{os.path.basename(wav)}"\r\n'
        f'Content-Type: {ctype}\r\n\r\n'.encode())
    parts.append(audio)
    parts.append(b'\r\n')
    field('response_format', 'text')
    # Pinned even though medium.en is English-only: it costs nothing and stops a
    # later multilingual model from language-detecting on noisy 8 kHz audio.
    field('language', lang)
    parts.append(f'--{boundary}--\r\n'.encode())

    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def server_ready(url: str = DEFAULT_URL, timeout: float = 2.0) -> bool:
    """True if the server answers. Loading medium.en into VRAM takes a few seconds."""
    try:
        with urllib.request.urlopen(f'{url}/', timeout=timeout) as r:
            return 200 <= r.status < 500
    except (urllib.error.URLError, OSError):
        return False


def transcribe_via_server(wav: str, url: str = DEFAULT_URL, lang: str = 'en',
                          timeout: float = 120.0) -> str:
    """POST one .wav and return normalised transcript text.

    Raises TransportError for anything retryable. The timeout is not optional:
    urllib blocks forever on a wedged server, where the old subprocess at least
    died with its process.
    """
    body, ctype = _multipart(wav, lang)
    req = urllib.request.Request(f'{url}/inference', data=body,
                                 headers={'Content-Type': ctype})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return transcript_text(r.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as e:
        raise TransportError(f'HTTP {e.code} from {url}') from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise TransportError(f'{type(e).__name__}: {e}') from e


def transcribe_via_cli(wav: str, model: str = DEFAULT_MODEL, threads: int = 8,
                       lang: str = 'en') -> str:
    """Fallback: the CPU binary, reading stdout rather than --output-txt.

    Deliberately the SAME model as the server. Falling back to small.en would be
    faster but would silently produce a two-model corpus, which is exactly what
    the switch to medium.en was meant to end.
    """
    if not os.path.exists(CPU_BIN):
        raise TransportError(f'no CPU fallback binary at {CPU_BIN}')
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = os.path.dirname(CPU_BIN)
    r = subprocess.run(
        [CPU_BIN, '-m', model, '-f', wav, '-l', lang, '--threads', str(threads),
         '--no-prints', '-nt'],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=600)
    if r.returncode != 0:
        raise TransportError(f'whisper-cli exited {r.returncode}')
    return transcript_text(r.stdout.decode('utf-8', 'replace'))


def transcribe(wav: str, outdir: str, *, url: str = DEFAULT_URL,
               model: str = DEFAULT_MODEL, lang: str = 'en', threads: int = 8,
               allow_cpu: bool = True, log=print) -> str:
    """Transcribe one .wav to <outdir>/<base>.txt. Returns OK, RETRY or FAIL.

    RETRY means the transport failed and the caller must not mark the file done.
    """
    txt = os.path.join(outdir, os.path.splitext(os.path.basename(wav))[0] + '.txt')
    try:
        text = transcribe_via_server(wav, url, lang)
    except TransportError as e:
        if not allow_cpu:
            log(f'stt: server unavailable ({e})')
            return RETRY
        # Loud, because CPU medium.en runs ~2.5 s/clip against the server's
        # 0.168 s — slower even than the old small.en pipeline. An operator who
        # only saw a growing backlog would have no idea why.
        log(f'stt: server unavailable ({e}); FALLING BACK TO CPU — ~15x slower')
        try:
            text = transcribe_via_cli(wav, model, threads, lang)
        except (TransportError, subprocess.SubprocessError, OSError) as e2:
            log(f'stt: CPU fallback failed for {os.path.basename(wav)}: {e2}')
            return RETRY
    try:
        write_transcript(txt, text)
    except OSError as e:
        log(f'stt: cannot write {txt}: {e}')
        return FAIL
    return OK
