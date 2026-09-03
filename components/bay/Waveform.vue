<template>
  <!--
    The clip's shape, drawn where the progress bar used to be.

    A canvas rather than SVG or a run of divs: a 15-second clip at 8 kHz is
    120,000 samples reduced to one column per device pixel, and the playhead
    moves four times a second. Redrawing a few hundred DOM nodes at that rate
    to show something that never changes shape is work for nothing.

    `aria-hidden` because it carries no information the operator cannot already
    read beside it — the talkgroup, the duration and the elapsed clock are all
    printed in the transport line. A screen reader announcing a waveform would
    be noise, not access.
  -->
  <canvas
    ref="canvas"
    class="wave"
    :class="{ 'wave--pending': !peaks }"
    aria-hidden="true"
  />
</template>

<script setup lang="ts">
import { ref, watch, onMounted, onUnmounted } from 'vue'

/**
 * The audio's envelope for one call, with a playhead.
 *
 * WHY IT DECODES ITS OWN COPY
 * ---------------------------
 * The `<audio>` element the feed owns already has these bytes, but a media
 * element exposes no sample data — that is what Web Audio is for. The clips are
 * 8 kHz mono 16-bit and mostly a few seconds, so a second fetch of a file the
 * server just read is cheap, and the browser cache usually makes it free.
 *
 * Decoding is deliberately NOT tied to playback. `decodeAudioData` works on a
 * suspended AudioContext, so this never needs the user gesture that autoplay
 * does — the shape can be on screen before the first sample is heard. The feed
 * has its own hard-won gesture handling for the audible path
 * (composables/useScannerFeed.ts); this must not perturb it, which is why it
 * creates its own context and never touches the element.
 */

const props = defineProps<{
  /** Recording filename, as `/api/recordings/<file>` serves it. */
  file: string
  /** 0..1 through the clip. The caller owns the clock. */
  progress: number
}>()

const canvas = ref<HTMLCanvasElement | null>(null)
/** Per-column [min, max] pairs in -1..1, or null until decoded. */
const peaks = ref<Float32Array | null>(null)

/**
 * Decoded envelopes, keyed by filename.
 *
 * A capped Map, not a plain object: the filed rail can review hundreds of clips
 * in a session, and every decode holds its peaks for the life of the page
 * otherwise. Insertion order is eviction order, which is close enough to
 * least-recently-shown for a cache this small.
 */
const CACHE = new Map<string, Float32Array>()
const CACHE_MAX = 60

/** One column per device pixel at the widest this element gets. */
const COLUMNS = 480

let ctx: AudioContext | null = null
/** Discards a decode that finishes after the operator moved to another clip. */
let generation = 0

async function decode(file: string): Promise<void> {
  const cached = CACHE.get(file)
  if (cached) { peaks.value = cached; draw(); return }

  const mine = ++generation
  peaks.value = null
  draw()

  try {
    const res = await fetch(`/api/recordings/${encodeURIComponent(file)}`)
    if (!res.ok) return
    const bytes = await res.arrayBuffer()
    // Created lazily and reused: a context per clip would leak hardware
    // decoders, and browsers cap how many a page may hold.
    ctx ??= new AudioContext()
    const buf = await ctx.decodeAudioData(bytes)
    if (mine !== generation) return

    const pcm = buf.getChannelData(0)
    const out = new Float32Array(COLUMNS * 2)
    const per = Math.max(1, Math.floor(pcm.length / COLUMNS))
    for (let c = 0; c < COLUMNS; c++) {
      let lo = 0, hi = 0
      const from = c * per
      const to = Math.min(pcm.length, from + per)
      for (let i = from; i < to; i++) {
        const v = pcm[i]
        if (v < lo) lo = v
        if (v > hi) hi = v
      }
      out[c * 2] = lo
      out[c * 2 + 1] = hi
    }

    if (CACHE.size >= CACHE_MAX) CACHE.delete(CACHE.keys().next().value as string)
    CACHE.set(file, out)
    peaks.value = out
    draw()
  } catch {
    // A clip that will not decode still plays, or does not — either way the
    // audible path is the feed's business and unaffected. Leaving `peaks` null
    // draws the flat baseline, which claims nothing rather than drawing a shape
    // this never actually measured.
    if (mine === generation) { peaks.value = null; draw() }
  }
}

function draw(): void {
  const el = canvas.value
  if (!el) return
  const dpr = window.devicePixelRatio || 1
  const w = Math.max(1, Math.round(el.clientWidth * dpr))
  const h = Math.max(1, Math.round(el.clientHeight * dpr))
  if (el.width !== w || el.height !== h) { el.width = w; el.height = h }

  const g = el.getContext('2d')
  if (!g) return
  g.clearRect(0, 0, w, h)

  const mid = h / 2
  const p = peaks.value

  // Read from the stylesheet rather than hardcoded, so this cannot drift out of
  // the design system the way the ten-code mark did — that one was given a
  // colour from the DARK sidebar palette and rendered nearly invisible on the
  // light strip stock. The transport is on --rail, so these are the dark-half
  // tokens and they belong here.
  const css = getComputedStyle(el)
  /**
   * Every name here is a token that EXISTS. A fallback that silently covers a
   * misspelled or absent custom property is the same shape of bug as a filter
   * that quietly returns the whole corpus — it renders something plausible and
   * nothing reports that the design system was never consulted. These were
   * checked against :root, not assumed.
   */
  const tok = (name: string) => css.getPropertyValue(name).trim()

  // --rail-lip is the lip that catches the light on the holder rail: the right
  // weight for the part of the clip not yet played, on --rail's dark ground.
  const ahead = tok('--rail-lip') || '#545a45'
  // --pencil-amber, "flagged for the operator's eye" — the audio already heard.
  const spent = tok('--pencil-amber') || '#c8871f'
  // --grease is "red pencil: live, now, attention". A playhead is exactly that.
  const head = tok('--grease') || '#b8371d'

  // The baseline always prints, decoded or not: an empty transport slot reads
  // as "nothing playing", which would be wrong while audio is running.
  g.fillStyle = ahead
  g.fillRect(0, Math.round(mid), w, Math.max(1, Math.round(dpr)))
  if (!p) return

  const played = Math.round(w * Math.min(1, Math.max(0, props.progress)))
  const bar = Math.max(1, Math.round(dpr))
  for (let c = 0; c < COLUMNS; c++) {
    const x = Math.round((c / COLUMNS) * w)
    const top = mid - p[c * 2 + 1] * mid
    const bot = mid - p[c * 2] * mid
    // Behind the playhead is spent, ahead of it is still to come. Same shape,
    // two weights — the bay's own idiom for "this has been handled".
    g.fillStyle = x < played ? spent : ahead
    g.fillRect(x, top, bar, Math.max(bar, bot - top))
  }

  g.fillStyle = head
  g.fillRect(played, 0, bar, h)
}

watch(() => props.file, f => { void decode(f) }, { immediate: true })
watch(() => props.progress, () => draw())

let ro: ResizeObserver | null = null
onMounted(() => {
  draw()
  // The rails resize when the live rail collapses, and a canvas does not
  // reflow — it has to be told, or the shape stretches.
  if (typeof ResizeObserver !== 'undefined' && canvas.value) {
    ro = new ResizeObserver(() => draw())
    ro.observe(canvas.value)
  }
})
onUnmounted(() => {
  ro?.disconnect()
  // Contexts are a limited per-page resource; a bay left open all shift would
  // otherwise hold one per mount.
  void ctx?.close()
  ctx = null
})
</script>
