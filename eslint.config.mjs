// @ts-check
import withNuxt from './.nuxt/eslint.config.mjs'

export default withNuxt(
  {
    // This repo is an SDR lab with a Nuxt app living at its root, so ESLint's
    // default "everything below cwd" scope sweeps in ~130 MB of vendored C/C++
    // projects that happen to ship .js — whisper.cpp's minified test bundles
    // alone produce 366 errors. Mirrors the `exclude` in tsconfig.json and the
    // `ignore` in nuxt.config.ts, both of which exist for the same reason.
    ignores: [
      'src/**',
      'tools/**',
      'recordings/**',
      'results/**',
      'captures/**',
      'models/**',
      'reference/**',
      'adp_brute/**',
      'web/**',
      'docs/**',
      '.nuxt/**',
      '.output/**',
      'node_modules/**',
    ],
  },
  {
    rules: {
      // CLAUDE.md: never suppress type errors, never leave an empty catch.
      // Promoted from warning to error so CI-style runs fail on them.
      '@typescript-eslint/no-explicit-any': 'error',
      '@typescript-eslint/ban-ts-comment': 'error',
      'no-empty': ['error', { allowEmptyCatch: false }],
    },
  },
)
