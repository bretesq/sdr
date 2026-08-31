export default defineNuxtConfig({
  compatibilityDate: '2025-01-01',
  devtools: { enabled: true },
  css: [
    'primeicons/primeicons.css',
    'primeflex/primeflex.css',
    '~/assets/css/compat.css', // must come AFTER primeflex
  ],
  build: {
    transpile: ['primevue'],
  },
  typescript: {
    strict: true,
    typeCheck: false,
  },
  // The repo root holds 221 MB / 6,463 files in recordings/ (growing during every
  // recording session), 81 MB in src/, 55 MB in results/. Nuxt does NOT read
  // .gitignore for its watch list, so without this the dev server registers
  // watchers on all of it — slow boot, ENOSPC risk on WSL2, and a watcher event
  // for every .wav the recorder flushes while you are using the app.
  ignore: [
    'recordings/**', 'results/**', 'captures/**', 'models/**',
    'src/**', 'reference/**', 'web/**', 'tools/**', 'docs/**', 'adp_brute/**',
    'lwin_*.tsv', 'lwin_*.txt', 'lwin_keys.json',
  ],
})
