import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs'
import { safeRecordingPath } from '~/server/utils/paths'

export default defineEventHandler((event) => {
  const name = getRouterParam(event, 'name') ?? ''
  const path = safeRecordingPath(name)

  if (!path || !existsSync(path)) {
    throw createError({ statusCode: 404, statusMessage: 'Not found' })
  }

  if (name.endsWith('.txt')) {
    setHeader(event, 'Content-Type', 'text/plain; charset=utf-8')
    return readFileSync(path, 'utf-8')
  }

  const size = statSync(path).size
  const range = getHeader(event, 'range')

  setHeader(event, 'Content-Type', 'audio/wav')
  setHeader(event, 'Accept-Ranges', 'bytes')

  if (!range) {
    setHeader(event, 'Content-Length', size)
    // Returning a Node Readable directly: h3 handles it, and sendStream() is
    // deprecated in h3 v1.
    return createReadStream(path)
  }

  const m = /^bytes=(\d*)-(\d*)$/.exec(range.trim())
  const unsatisfiable = (): string => {
    setResponseStatus(event, 416)
    setHeader(event, 'Content-Range', `bytes */${size}`)
    return ''
  }

  // `bytes=-` with both sides empty is malformed, not a whole-file request.
  if (!m || (!m[1] && !m[2])) return unsatisfiable()

  let start: number
  let end: number

  if (!m[1]) {
    // Suffix range: `bytes=-500` means the LAST 500 bytes (RFC 7233 §2.1),
    // NOT bytes 0-500. Getting this wrong serves the head of the file under a
    // self-consistent Content-Range — wrong audio, no error, silent.
    const suffixLength = Number(m[2])
    start = Math.max(0, size - suffixLength)
    end = size - 1
  } else {
    start = Number(m[1])
    end = m[2] ? Math.min(Number(m[2]), size - 1) : size - 1
  }

  if (start >= size || end < start) return unsatisfiable()

  setResponseStatus(event, 206)
  setHeader(event, 'Content-Range', `bytes ${start}-${end}/${size}`)
  setHeader(event, 'Content-Length', end - start + 1)
  return createReadStream(path, { start, end })
})
