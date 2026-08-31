import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { loadJSON, scanRecordings, mergeCalls } from './files'
import type { Recording, TalkgroupEntry } from './files'
import { recordingsDir, referenceDir } from './paths'

/** True only for a genuine "file does not exist" errno. */
function isMissingFile(err: unknown): boolean {
  return err instanceof Error && 'code' in err && err.code === 'ENOENT'
}

/**
 * Attach the .txt transcript for each recording.
 *
 * Necessary because calls.json carries no transcript field: stt_watch.py merges
 * transcripts in, then udp_audio_record.py rewrites the file at session end and
 * clobbers them. The .txt files on disk are the only durable copy.
 *
 * Cheap enough to do per request — 3,231 files but only ~154 KB of text total
 * (~48 chars average), measured at ~80 ms. Transcripts must be in the payload
 * so the client can search them, which is the whole point of --stt.
 */
function attachTranscripts(recordings: Recording[]): Recording[] {
  const dir = recordingsDir()
  return recordings.map((rec) => {
    // The .txt file is AUTHORITATIVE — no early return on rec.transcript.
    // calls.json has no transcript key today, but if udp_audio_record.py is
    // ever fixed to stop clobbering stt_watch.py's merges, a `if (rec.transcript)
    // return rec` guard here would silently flip which source wins. Read the
    // file unconditionally; it costs ~80 ms for all 3,231.
    try {
      const txt = readFileSync(join(dir, rec.file.replace(/\.wav$/, '.txt')), 'utf-8').trim()
      return txt ? { ...rec, transcript: txt } : rec
    } catch (err) {
      // No transcript yet — expected for a fresh recording. Anything else
      // (EACCES, EIO, EISDIR) is not a normal state and must not be swallowed.
      if (isMissingFile(err)) return rec
      throw err
    }
  })
}

export function allRecordings(): Recording[] {
  const tgdb = loadJSON<Record<string, TalkgroupEntry>>(
    join(referenceDir(), 'lwin_talkgroups.json'), {},
  )
  const calls = loadJSON<unknown>(join(recordingsDir(), 'calls.json'), [])
  return attachTranscripts(mergeCalls(scanRecordings(recordingsDir(), tgdb), calls))
}
