---
version: 1
slug: "pages-index-vue"
primary_target: "pages/index.vue"
related_targets: ["components/ScannerFeed.vue","components/RecordingsList.vue","components/ListenControl.vue","components/TalkgroupBrowser.vue"]
---

Scope: the whole console at `pages/index.vue` and its panels. Visitor mode: Operate.

Audience: one operator who owns the receiver. Job: hear what is on the air now, and read
what was said. Desktop at the bench, phone as a real listening client away from it.
Constraints: single radio, one session at a time, often idle; bursty traffic; density and
non-dashboard character are explicit requirements.

## Direction contract

THESIS: A strip bay, not a dashboard — one printed strip per call, filed between rails as
its state changes. Refuses the panel grid every SDR front end ships.

OWN-WORLD: ATC flight progress strips on tinted card, butted edge to edge in metal
holder rails on a dull olive-graphite bay. Stock tint carries state (buff live, drab filed,
cool grey locked); square corners, printed hairlines, one dark ink, grease-pencil red for
annotation. No cards, shadows, or rounded boxes: the rail is the only container.

STORY: The operator sees the live strip, hears it, reads its transcript printed on the
strip itself, and scrolls back through filed strips without changing mode.

FIRST VIEWPORT: Live rail across the top holding the playing strip; the archive rail fills
everything below, strips butted and dense. The comm stack sits top-left as active/standby
with the arm control. Transcripts print on their own strip, never in a side pane. Mobile
rotates to one vertical rail with the playing strip pinned in the thumb zone.

FORM: The Strip Bay, ATC flight progress strips; my top-ranked grounded candidate, taken
as IMPECCABLE'S PICK over the roll's assignment; seed key 3b8ac847.

SIGNATURE INTERACTION: Filing — a finished call's strip slides on its rail from live to
archive under weight, never a fade; a locked strip cocks out at an angle and never files.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance

## Unresolved

Whether the talkgroup reference browser (4,163 rows) stays on this surface or earns its
own; it is the one panel the strip grammar does not obviously fit.
