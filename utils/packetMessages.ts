/**
 * How a packet-data message reads on a strip.
 *
 * Pure, and here rather than inside DataRail.vue, because the wording carries
 * claims that have to stay true: which stock a message prints on asserts
 * whether we proved its payload readable, and the sentence asserts what the
 * system did. Both are worth a test, and a function inside `<script setup>`
 * cannot have one.
 */

/** The shape the rail needs. A subset of server/utils/queries' Packet. */
export interface PacketLike {
  llid: number | null
  clear: boolean
  app: string | null
  appKind: string | null
  dstIp: string | null
  blksClaimed: number | null
  blksRecovered: number | null
}

/**
 * Which stock a message prints on.
 *
 * `clear` when the payload's own IPv4 header checksum validated — the bay's
 * existing meaning for unencrypted traffic, and here it is proof rather than
 * an assumption.
 *
 * `void` — "recorded but nothing decoded" — for everything else. Deliberately
 * NOT `locked`, which means encrypted under a key we do not hold: this traffic
 * is not encrypted, and most of it is acknowledgements carrying no payload at
 * all. Locked stock would claim an encryption we specifically disproved.
 */
export function packetStock(p: PacketLike): 'clear' | 'void' {
  return p.clear ? 'clear' : 'void'
}

/** The radio, in the same hex the console uses for call source addresses. */
export function radioLabel(p: PacketLike): string {
  return p.llid === null ? '—' : p.llid.toString(16).padStart(6, '0')
}

/**
 * The strip's headline.
 *
 * Falls back to naming WHICH kind of undecoded message it is rather than
 * leaving the row blank: a row with no headline reads as a rendering fault.
 */
export function packetHeadline(p: PacketLike): string {
  if (p.appKind) return p.appKind
  return p.dstIp ? 'packet data, not decoded' : 'acknowledgement'
}

/**
 * What the system did, in the operator's terms.
 *
 * The LRRP sentence names the limit deliberately. A console that said only
 * "asked for its location" would imply the answer is somewhere in this
 * database, and it never can be: replies travel on the uplink, 30 MHz above
 * the downlink these receivers are on.
 */
export function packetSentence(p: PacketLike): string {
  if (p.app === 'ARS') {
    return `The system acknowledged this radio's registration${
      p.dstIp ? `, addressed to ${p.dstIp}` : ''}.`
  }
  if (p.app === 'LRRP') {
    return 'The system asked this radio to start reporting its location. '
      + 'The reply travels on the uplink, which no receiver here can tune.'
  }
  if (p.blksClaimed && p.blksRecovered !== null && p.blksRecovered < p.blksClaimed) {
    return `Carried ${p.blksClaimed} data blocks; ${p.blksRecovered} survived `
      + 'error correction, so the message could not be read.'
  }
  return 'A short acknowledgement with no payload.'
}

/** 24-hour clock, seconds included: these arrive seconds apart. */
export function packetClock(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}
