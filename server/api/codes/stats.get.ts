import { codeStats, type CodeStatsQuery } from '~/server/utils/queries'

/**
 * Aggregate radio-code counts. Powers the code filter's option list and any
 * "which codes are busiest" view, without a separate analytics page.
 */
export default defineEventHandler((event) => {
  const q = getQuery(event)

  const num = (v: unknown): number | undefined => {
    const n = Number(v)
    return v === undefined || Number.isNaN(n) ? undefined : n
  }

  const conf = typeof q.minConfidence === 'string' ? q.minConfidence : undefined
  const query: CodeStatsQuery = {
    since: num(q.since),
    until: num(q.until),
    tgid: num(q.tgid),
    cat: typeof q.cat === 'string' ? q.cat : undefined,
    minConfidence: conf === 'high' || conf === 'medium' || conf === 'low'
      ? conf
      : undefined,
  }

  return codeStats(query)
})
