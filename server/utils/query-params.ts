/**
 * Parses an optional numeric query-string parameter.
 *
 * `Number('')` is `0`, not `NaN`, so a naive `typeof v === 'string' ? Number(v)
 * : undefined` turns a CLEARED filter (`?tgid=`, `?until=`) into a real filter
 * on `tgid = 0` / `until = 0` instead of no filter at all — exactly what a
 * browser sends when a user empties a field. Treat an empty (or
 * whitespace-only) string the same as a missing param, while still letting a
 * genuine zero through: `parseNumberParam('0') === 0`, not `undefined`.
 *
 * Extracted rather than left as a route-local closure so it can be exercised
 * directly under vitest: `getQuery`/`defineEventHandler` are Nitro
 * auto-imports and undefined in a plain vitest environment, the same reason
 * server/utils/guards.ts pulls its checks out of the handlers that use them.
 */
export function parseNumberParam(v: unknown): number | undefined {
  if (typeof v !== 'string' || v.trim() === '') return undefined
  const n = Number(v)
  return Number.isNaN(n) ? undefined : n
}
