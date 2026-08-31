import { readFileSync } from 'node:fs'
import { loadJSON } from './files'
import type { TalkgroupEntry } from './files'

const BR_AREA_KEYWORDS = [
  'East Baton Rouge', 'Baton Rouge', 'LSU', 'Southern University',
  'State Police - Troop A', 'West Baton Rouge', 'Livingston', 'Ascension',
  'Iberville', 'Feliciana', 'Pointe Coupee', 'EMS Agencies',
  'Wildlife and Fisheries',
]

/**
 * The DB is an object keyed by tgid-as-string; the value objects carry NO tgid.
 * Synthesize it from the key. Sorting is explicit: relying on Object.values()
 * order works only because V8 iterates integer-like keys numerically, which is
 * an engine detail, not a guarantee — and server.py sorted explicitly.
 */
export function loadTalkgroups(path: string): TalkgroupEntry[] {
  const raw = loadJSON<Record<string, Omit<TalkgroupEntry, 'tgid'>>>(path, {})

  return Object.entries(raw)
    .map(([key, v]) => ({ tgid: Number.parseInt(key, 10), ...v }))
    .filter(tg => Number.isFinite(tg.tgid))
    .sort((a, b) => a.tgid - b.tgid)
}

export function loadWhitelist(path: string): Set<number> {
  try {
    const text = readFileSync(path, 'utf-8')
    const ids = text
      .split('\n')
      .map(line => line.trim().split(/[\s,]/)[0])
      .map(tok => Number.parseInt(tok, 10))
      .filter(n => Number.isFinite(n))
    return new Set(ids)
  } catch {
    return new Set()
  }
}

export function filterByArea(
  tgs: TalkgroupEntry[],
  area: 'br' | 'all',
): TalkgroupEntry[] {
  if (area === 'all') return tgs
  return tgs.filter(tg =>
    BR_AREA_KEYWORDS.some(k => (tg.cat ?? '').includes(k)),
  )
}

export function filterTalkgroups(
  tgs: TalkgroupEntry[],
  opts: { category?: string; text?: string; enc?: string },
): TalkgroupEntry[] {
  let out = tgs

  if (opts.category) {
    out = out.filter(tg => tg.cat === opts.category)
  }

  // The old console's talkgroups panel had its own encryption filter — needed to
  // answer "show me the encrypted talkgroups in EBR", a core question here.
  if (opts.enc && opts.enc !== 'all') {
    out = out.filter(tg => tg.enc === opts.enc)
  }

  if (opts.text) {
    const q = opts.text.toLowerCase()
    // server.py searched [alpha, desc, cat, tag, tgid] — match that.
    out = out.filter(tg =>
      String(tg.tgid).includes(q) ||
      (tg.alpha ?? '').toLowerCase().includes(q) ||
      (tg.desc ?? '').toLowerCase().includes(q) ||
      (tg.cat ?? '').toLowerCase().includes(q) ||
      (tg.tag ?? '').toLowerCase().includes(q),
    )
  }

  return out
}
