import { codeStats, type CodeStatsQuery } from '~/server/utils/queries'
import { parseNumberParam } from '~/server/utils/query-params'

/**
 * Aggregate radio-code counts. Powers the code filter's option list and any
 * "which codes are busiest" view, without a separate analytics page.
 */
export default defineEventHandler((event) => {
  const q = getQuery(event)

  const conf = typeof q.minConfidence === 'string' ? q.minConfidence : undefined
  const query: CodeStatsQuery = {
    since: parseNumberParam(q.since),
    until: parseNumberParam(q.until),
    tgid: parseNumberParam(q.tgid),
    cat: typeof q.cat === 'string' ? q.cat : undefined,
    minConfidence: conf === 'high' || conf === 'medium' || conf === 'low'
      ? conf
      : undefined,
  }

  return codeStats(query)
})
