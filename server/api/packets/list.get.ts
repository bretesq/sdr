import { listPackets, packetSummary } from '~/server/utils/queries'

/**
 * The only `app` values that exist. A Set, not a cast — the point is to REFUSE
 * an unrecognised filter rather than silently drop it.
 *
 * Same reasoning as recordings/list.get.ts's `encState`: dropping a misspelled
 * `?app=LRPP` would answer with every row and a `total` that looks
 * authoritative, and the caller could not tell the filter had been discarded.
 * Refusing names the valid values instead.
 *
 * TMS is deliberately absent. Zero TMS packets have ever been observed on this
 * system, so accepting the filter would imply a service we have no evidence
 * of; a caller asking for it should get the list of what actually exists.
 */
const APPS = new Set<string>(['ARS', 'LRRP'])

function app(raw: unknown): string | undefined {
  if (raw === undefined) return undefined
  const v = String(raw)
  if (!APPS.has(v)) {
    throw createError({
      statusCode: 400,
      statusMessage: `Unknown app "${v}". Use one of: ${[...APPS].join(', ')}`,
    })
  }
  return v
}

/**
 * A run of digits, validated on SHAPE rather than on coercing to a number.
 *
 * The trap recordings/list.get.ts documents at length applies identically
 * here: `?afterId=abc` becomes NaN, node:sqlite binds NaN as NULL, `id > NULL`
 * is NULL, and the feed returns zero rows forever with no error and no log
 * line. `Number.isInteger(Number(''))` would also accept an empty string as a
 * real cursor of 0.
 */
function digits(raw: unknown): number | undefined {
  if (raw === undefined) return undefined
  const v = String(raw)
  if (!/^\d+$/.test(v)) {
    throw createError({
      statusCode: 400,
      statusMessage: `Expected a whole number, got "${v}"`,
    })
  }
  return Number.parseInt(v, 10)
}

export default defineEventHandler((event) => {
  const q = getQuery(event)

  const { rows, total, maxId } = listPackets({
    limit: digits(q.limit),
    afterId: digits(q.afterId),
    llid: digits(q.llid),
    app: app(q.app),
    // Presence-based, so `?clearOnly` with no value works. `=0` and `=false`
    // are honoured because a UI toggle that serialises false must be able to
    // turn the filter OFF, not merely omit it.
    clearOnly: q.clearOnly !== undefined
      && q.clearOnly !== '0' && q.clearOnly !== 'false',
  })

  return {
    success: true,
    data: {
      rows,
      total,
      maxId,
      // Cheap enough to include on every page, and it is what the header
      // needs: without it a client showing "N radios" would have to page the
      // whole table to count them.
      summary: packetSummary(),
    },
  }
})
