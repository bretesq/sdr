export default defineNuxtConfig({
  compatibilityDate: '2025-01-01',
  devtools: { enabled: true },
  modules: ['@nuxt/eslint'],
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
  vite: {
    server: {
      // Vite 5.4.12+ rejects requests whose Host header it does not recognise
      // (a DNS-rebinding guard), so reaching the dev server by hostname rather
      // than by IP returns "Blocked request. This host is not allowed."
      // localhost and bare IPs are permitted by default; named hosts are not.
      //
      // A leading dot matches the domain and all its subdomains. This is a
      // LAN-only console, so the allowlist is deliberately narrow rather than
      // `true` — `true` disables the check entirely for any host that can
      // resolve to this box.
      allowedHosts: ['monarch.esquivel.io', '.esquivel.io'],
    },
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
