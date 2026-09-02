export default defineNuxtConfig({
  compatibilityDate: '2025-01-01',
  devtools: { enabled: true },
  modules: ['@nuxt/eslint', '@nuxt/fonts'],
  // Self-hosted, downloaded at build time — no CDN, so the console still works
  // on an isolated LAN. Nuxt + Aura set no font-family at all, which lands on
  // the browser default (Times New Roman) and looks like a 1998 intranet.
  // The Strip Bay's three voices. Barlow Condensed is the printed form label,
  // Barlow Semi Condensed carries transcript prose at a readable measure, and
  // Sometype Mono is reserved for measurement — talkgroup ids, clocks,
  // durations, key ids — never as a costume for "technical".
  fonts: {
    families: [
      { name: 'Barlow Condensed', provider: 'google', weights: [500, 600, 700] },
      { name: 'Barlow Semi Condensed', provider: 'google', weights: [400, 500, 600] },
      { name: 'Sometype Mono', provider: 'google', weights: [400, 500, 700] },
    ],
  },
  // PrimeVue's Aura theme, primeflex's grid and the compat shim built the
  // previous console's look. This surface is a committed world of its own, so
  // it carries no framework theme; bay.css is the whole design system.
  css: ['~/assets/css/bay.css'],
  build: {
    transpile: ['primevue'],
  },
  typescript: {
    strict: true,
    typeCheck: false,
  },
  vite: {
    server: {
      // Vite 5.4.12+ rejects requests whose Host header it does not recognise
      // (a DNS-rebinding guard), so reaching the dev server by hostname rather
      // than by IP returns "Blocked request. This host is not allowed."
      // localhost and bare IPs are permitted by default; named hosts are not.
      //
      // Set NUXT_ALLOWED_HOSTS in .env (gitignored) to a comma-separated list,
      // e.g. `NUXT_ALLOWED_HOSTS=box.example.com,.example.com` — a leading dot
      // matches the domain and all its subdomains. Kept out of the repo so a
      // public checkout carries nobody's hostname.
      //
      // Prefer naming hosts over `true`, which disables the check entirely for
      // any host that can be made to resolve to this box.
      //
      // SCOPE — this is a DEV-SERVER setting only. Nitro's production server
      // (`node .output/server/index.mjs`) performs no Host check at all, so in
      // production this key is inert and every hostname reaches the app. Do not
      // treat it as a security control: it exists so that reaching the dev
      // server by name works, and the real protection against a hostile page
      // driving this console is the same-origin guard in server/utils/guards.ts,
      // which applies in both dev and production. Vite's own check would not
      // have helped anyway — it passes any bare IPv4 literal unconditionally.
      allowedHosts: (process.env.NUXT_ALLOWED_HOSTS ?? '')
        .split(',')
        .map(h => h.trim())
        .filter(Boolean),
    },
  },
  // The repo root holds 221 MB / 6,463 files in recordings/ (growing during every
  // recording session), 81 MB in src/, 55 MB in results/. Nuxt does NOT read
  // .gitignore for its watch list, so without this the dev server registers
  // watchers on all of it — slow boot, ENOSPC risk on WSL2, and a watcher event
  // for every .wav the recorder flushes while you are using the app.
  //
  // Anchored with './'. A bare 'recordings/**' matches that segment at ANY
  // depth, so it also covered server/api/recordings/ and Nuxt stopped watching
  // the recordings API routes — a new file there was not picked up until a
  // full restart. Same root cause as .gitignore:2, which had untracked those
  // routes outright.
  ignore: [
    './recordings/**', './results/**', './captures/**', './models/**',
    './src/**', './reference/**', './web/**', './tools/**', './docs/**',
    './adp_brute/**',
    'lwin_*.tsv', 'lwin_*.txt', 'lwin_keys.json',
  ],
})
