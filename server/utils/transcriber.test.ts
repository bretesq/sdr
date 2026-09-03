import { describe, it, expect } from 'vitest'
import { startTranscriber, stopTranscriber } from './transcriber'

/**
 * Only the in-container path is exercised here, by design.
 *
 * With SDR_IN_CONTAINER unset (the normal state under `vitest`), the host path
 * of startTranscriber() spawns a REAL stt_watch.py against the live
 * recordings/ directory — a second watcher racing the compose-managed one on
 * the same .txt files — and the host path of stopTranscriber() runs a REAL
 * `pkill -INT -f stt_watch.py`, which (through `pid: host` on this box) would
 * signal the actual containerized watcher serving live transcription. Neither
 * function may be called here without the variable set to '1'. This mirrors
 * processes.test.ts's "container mode" block, which never calls
 * startListening() on the host path for the identical reason.
 */
describe('transcriber container guard', () => {
  it('startTranscriber refuses in-container and names the recovery command', () => {
    process.env.SDR_IN_CONTAINER = '1'
    try {
      expect(() => startTranscriber()).toThrow(/compose/i)
      expect(() => startTranscriber()).toThrow(/stack\.sh restart stt-watch/)
    } finally {
      delete process.env.SDR_IN_CONTAINER
    }
  })

  it('stopTranscriber refuses in-container and names the recovery command', () => {
    process.env.SDR_IN_CONTAINER = '1'
    try {
      // Explains the refusal is about non-stickiness, not danger, and points
      // at the compose command that actually holds the watcher down.
      expect(() => stopTranscriber()).toThrow(/restart: unless-stopped/)
      expect(() => stopTranscriber()).toThrow(/docker compose stop stt-watch/)
    } finally {
      delete process.env.SDR_IN_CONTAINER
    }
  })
})
