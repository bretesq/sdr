#!/usr/bin/env python3
"""Transport tests for the whisper-server backend.

The cases that matter here are not "does HTTP work" but the two invariants that
stt_watch.py's idempotency rests on:

  1. A .txt is written even for silence, because its mere existence is the
     "already transcribed" sentinel. Skip it and every silent clip is re-POSTed
     forever.
  2. Text is normalised to the exact bytes whisper-cli --output-txt produced,
     because 4,444 rows in sdr.db came from the old path and both feed the same
     tencodes extractor.

Everything is exercised through pure functions or a stub server, so the suite
needs neither a GPU nor a running container.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stt_backend  # noqa: E402


class TranscriptText(unittest.TestCase):
    """whisper-server returns ' Turn.\\n'; whisper-cli wrote 'turn.\\n'."""

    def test_strips_the_leading_token_space(self):
        self.assertEqual(stt_backend.transcript_text(' Turn.\n'), 'Turn.\n')

    def test_one_segment_per_line_trailing_newline(self):
        self.assertEqual(
            stt_backend.transcript_text(' A one.\n B two.\n'),
            'A one.\nB two.\n')

    def test_silence_is_the_empty_string(self):
        for body in ('', '\n', ' \n \n', '\n\n\n'):
            self.assertEqual(stt_backend.transcript_text(body), '')

    def test_blank_segments_are_dropped_not_kept_as_gaps(self):
        self.assertEqual(
            stt_backend.transcript_text(' A one.\n\n \n B two.\n'),
            'A one.\nB two.\n')

    def test_internal_whitespace_survives(self):
        # Only the edges are touched; "10-4" and friends must pass through.
        self.assertEqual(
            stt_backend.transcript_text('  10-4, 10-15 times one.  \n'),
            '10-4, 10-15 times one.\n')


class WriteTranscript(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def path(self, name='TG1_A_20260101-000000.txt'):
        return os.path.join(self.d, name)

    def test_writes_an_empty_file_for_silence(self):
        # The sentinel property. Without the file, the watcher never stops
        # retrying this clip.
        p = self.path()
        stt_backend.write_transcript(p, '')
        self.assertTrue(os.path.exists(p))
        self.assertEqual(os.path.getsize(p), 0)

    def test_round_trips_content(self):
        p = self.path()
        stt_backend.write_transcript(p, '10-4.\n')
        with open(p) as f:
            self.assertEqual(f.read(), '10-4.\n')

    def test_overwrite_replaces_rather_than_appends(self):
        p = self.path()
        stt_backend.write_transcript(p, 'first attempt.\n')
        stt_backend.write_transcript(p, 'second.\n')
        with open(p) as f:
            self.assertEqual(f.read(), 'second.\n')

    def test_leaves_no_temp_files_behind(self):
        stt_backend.write_transcript(self.path(), 'x\n')
        leftovers = [f for f in os.listdir(self.d) if f.startswith('.stt-')]
        self.assertEqual(leftovers, [])

    def test_failure_does_not_leave_a_partial_txt(self):
        # A .txt that exists but is wrong is worse than none: it means "done".
        p = self.path()
        with unittest.mock.patch('os.replace', side_effect=OSError('boom')):
            with self.assertRaises(OSError):
                stt_backend.write_transcript(p, 'half')
        self.assertFalse(os.path.exists(p))
        self.assertEqual([f for f in os.listdir(self.d) if f.startswith('.stt-')], [])


class Multipart(unittest.TestCase):
    def test_pins_language_and_text_format(self):
        wav = os.path.join(tempfile.mkdtemp(), 'TG1_A_20260101-000000.wav')
        with open(wav, 'wb') as f:
            f.write(b'RIFFfake')
        body, ctype = stt_backend._multipart(wav, 'en')
        self.assertIn('multipart/form-data; boundary=', ctype)
        # language is pinned so a later multilingual model cannot language-detect
        # on noisy 8 kHz audio.
        self.assertIn(b'name="language"', body)
        self.assertIn(b'en', body)
        self.assertIn(b'name="response_format"', body)
        self.assertIn(b'text', body)
        self.assertIn(b'RIFFfake', body)


class Outcomes(unittest.TestCase):
    """transcribe() must distinguish retryable transport loss from real failure."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.wav = os.path.join(self.d, 'TG1_A_20260101-000000.wav')
        with open(self.wav, 'wb') as f:
            f.write(b'RIFFfake')

    def test_server_success_writes_txt_and_reports_ok(self):
        with unittest.mock.patch.object(stt_backend, 'transcribe_via_server',
                                        return_value='10-4.\n'):
            r = stt_backend.transcribe(self.wav, self.d, log=lambda m: None)
        self.assertEqual(r, stt_backend.OK)
        with open(os.path.join(self.d, 'TG1_A_20260101-000000.txt')) as f:
            self.assertEqual(f.read(), '10-4.\n')

    def test_server_down_without_fallback_is_retry_and_writes_nothing(self):
        with unittest.mock.patch.object(
                stt_backend, 'transcribe_via_server',
                side_effect=stt_backend.TransportError('refused')):
            r = stt_backend.transcribe(self.wav, self.d, allow_cpu=False,
                                       log=lambda m: None)
        self.assertEqual(r, stt_backend.RETRY)
        # No .txt: the file must be picked up again next pass.
        self.assertFalse(os.path.exists(
            os.path.join(self.d, 'TG1_A_20260101-000000.txt')))

    def test_server_down_falls_back_to_cpu_with_the_same_model(self):
        seen = {}

        def fake_cli(wav, model, threads, lang):
            seen['model'] = model
            return 'from cpu.\n'

        with unittest.mock.patch.object(
                stt_backend, 'transcribe_via_server',
                side_effect=stt_backend.TransportError('refused')), \
             unittest.mock.patch.object(stt_backend, 'transcribe_via_cli', fake_cli):
            r = stt_backend.transcribe(self.wav, self.d, log=lambda m: None)
        self.assertEqual(r, stt_backend.OK)
        # Falling back to a *different* (faster, worse) model would silently
        # produce a two-model corpus.
        self.assertEqual(seen['model'], stt_backend.DEFAULT_MODEL)

    def test_both_transports_failing_is_retry(self):
        with unittest.mock.patch.object(
                stt_backend, 'transcribe_via_server',
                side_effect=stt_backend.TransportError('refused')), \
             unittest.mock.patch.object(
                stt_backend, 'transcribe_via_cli',
                side_effect=stt_backend.TransportError('no binary')):
            r = stt_backend.transcribe(self.wav, self.d, log=lambda m: None)
        self.assertEqual(r, stt_backend.RETRY)

    def test_silence_still_counts_as_ok_and_creates_the_sentinel(self):
        with unittest.mock.patch.object(stt_backend, 'transcribe_via_server',
                                        return_value=''):
            r = stt_backend.transcribe(self.wav, self.d, log=lambda m: None)
        self.assertEqual(r, stt_backend.OK)
        self.assertTrue(os.path.exists(
            os.path.join(self.d, 'TG1_A_20260101-000000.txt')))


class MergeEmptyTranscript(unittest.TestCase):
    """The live indexer must mirror an empty .txt, like the bulk one does.

    Both paths write '' rather than skipping, so a clip whisper heard nothing in
    cannot keep an earlier model's transcript in its row while the .txt beside
    it — the documented durable copy — is blank.
    """

    def test_stt_watch_and_stt_transcribe_agree_on_empty(self):
        import inspect
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import stt_watch
        import stt_transcribe

        # Neither may carry the old "skip empty" early return.
        watch_src = inspect.getsource(stt_watch.merge_transcript)
        bulk_src = inspect.getsource(stt_transcribe.merge_transcripts)
        self.assertNotIn('if not text:\n        return', watch_src)
        self.assertNotIn('if not text:\n                continue', bulk_src)


if __name__ == '__main__':
    unittest.main()
