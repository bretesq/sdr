import { CAPTURE_PRESETS, CAPTURE_PRESET_LABELS } from '~/utils/listenControl'

/**
 * The selection vocabulary a capture-start UI needs: the talkgroup presets and
 * the two area scopes.
 *
 * The preset list is DERIVED from `CAPTURE_PRESETS` rather than written out
 * here, which it was until presets became selectable from the console. While
 * `pd` was the only preset any console could actually run, a drifted copy in
 * this endpoint was harmless — nothing could act on the other eight anyway.
 * Now that every one of them starts a real capture, four hand-maintained
 * copies of the same nine names (this file, the bay's picker,
 * `buildControlRequest()`'s gate, and `capture_control.py`'s `PRESET_ARGV`)
 * is four chances to offer an operator a preset one of the layers below will
 * refuse. Two remain by necessity — argv construction belongs to the Python
 * side and it validates independently — and those two are pinned to each
 * other by a test that parses `make_whitelist.py`'s own `PRESETS` dict.
 *
 * The labels come from the same record for the same reason: an endpoint and a
 * picker that describe the same preset differently is its own small lie.
 */
export default defineEventHandler(() => {
  return {
    success: true,
    data: {
      presets: CAPTURE_PRESETS.map(value => ({ value, label: CAPTURE_PRESET_LABELS[value] })),
      areas: [
        { value: 'br', label: 'Baton Rouge Area' },
        { value: 'all', label: 'Statewide' },
      ],
    },
  }
})
