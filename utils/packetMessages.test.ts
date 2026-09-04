import { describe, it, expect } from 'vitest'
import {
  packetStock, radioLabel, packetHeadline, packetSentence, packetClock,
} from './packetMessages'
import type { PacketLike } from './packetMessages'

/** A real ARS acknowledgement, as stored by scripts/import_packets.py. */
const ARS: PacketLike = {
  llid: 219524, clear: true, app: 'ARS',
  appKind: 'registration acknowledgement',
  dstIp: '172.16.95.29', blksClaimed: 3, blksRecovered: 3,
}

/** A real LRRP location poll. */
const LRRP: PacketLike = {
  llid: 218586, clear: true, app: 'LRRP',
  appKind: 'triggered location start request',
  dstIp: '172.16.93.225', blksClaimed: 4, blksRecovered: 4,
}

/** A real response PDU: no IP layer at all, so nothing to prove readable. */
const RESPONSE: PacketLike = {
  llid: 219521, clear: false, app: null, appKind: null,
  dstIp: null, blksClaimed: 0, blksRecovered: 0,
}

describe('packetStock', () => {
  it('prints a checksum-validated payload on clear stock', () => {
    expect(packetStock(ARS)).toBe('clear')
    expect(packetStock(LRRP)).toBe('clear')
  })

  it('never prints on LOCKED stock, because none of this is encrypted', () => {
    // The load-bearing assertion of this whole panel. `locked` means
    // "encrypted under a key we do not hold" everywhere else in the bay, and
    // this traffic was specifically PROVED cleartext or carries no payload at
    // all. Reaching for locked here would publish a false claim in the one
    // place an operator would trust it.
    for (const p of [ARS, LRRP, RESPONSE]) {
      expect(packetStock(p)).not.toBe('locked')
    }
  })

  it('prints an unproven payload on void — recorded, nothing decoded', () => {
    expect(packetStock(RESPONSE)).toBe('void')
  })
})

describe('radioLabel', () => {
  it('uses the same hex the console uses for call source addresses', () => {
    expect(radioLabel(ARS)).toBe('035984')
    expect(radioLabel(LRRP)).toBe('0355da')
  })

  it('pads to six digits so a column of them aligns', () => {
    expect(radioLabel({ ...ARS, llid: 0x2d })).toBe('00002d')
  })

  it('shows an em dash rather than 0 when there is no radio', () => {
    expect(radioLabel({ ...ARS, llid: null })).toBe('—')
  })
})

describe('packetHeadline', () => {
  it('uses the decoded message type when there is one', () => {
    expect(packetHeadline(ARS)).toBe('registration acknowledgement')
  })

  it('names which kind of undecoded message it is, never blank', () => {
    // A blank headline reads as a rendering fault rather than as a fact about
    // the traffic.
    expect(packetHeadline(RESPONSE)).toBe('acknowledgement')
    expect(packetHeadline({ ...RESPONSE, dstIp: '172.16.1.1' }))
      .toBe('packet data, not decoded')
  })
})

describe('packetSentence', () => {
  it('says what the system did, addressed to whom', () => {
    expect(packetSentence(ARS)).toContain('acknowledged')
    expect(packetSentence(ARS)).toContain('172.16.95.29')
  })

  it('names the uplink limit on every location poll', () => {
    // Without this the console implies the answer is somewhere in the
    // database. It never can be: replies travel 30 MHz up, outside every
    // window these receivers can tune.
    const s = packetSentence(LRRP)
    expect(s).toContain('start reporting its location')
    expect(s).toContain('uplink')
  })

  it('reports partial FEC recovery as the reason a message is unreadable', () => {
    const lossy = { ...RESPONSE, app: null, blksClaimed: 4, blksRecovered: 1 }
    const s = packetSentence(lossy)
    expect(s).toContain('4 data blocks')
    expect(s).toContain('1 survived')
  })

  it('does not claim FEC loss when nothing was claimed', () => {
    expect(packetSentence(RESPONSE)).toBe('A short acknowledgement with no payload.')
  })
})

describe('packetClock', () => {
  it('keeps seconds, because these arrive seconds apart', () => {
    expect(packetClock(1788535473.743456)).toMatch(/^\d{2}:\d{2}:\d{2}$/)
  })
})
