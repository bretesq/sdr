import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    // `utils/` holds the client-side pure helpers; server/ holds the rest.
    include: ['server/**/*.test.ts', 'utils/**/*.test.ts'],
    environment: 'node',
    server: {
      deps: {
        // `node:sqlite` landed in Node 22 and is not in Vite 5's builtin list,
        // so Vite strips the `node:` prefix and tries to resolve a package
        // called "sqlite", failing with "Does the file exist?". Marking it
        // external hands it back to Node's own resolver.
        external: ['node:sqlite'],
      },
    },
  },
  ssr: {
    external: ['node:sqlite'],
  },
})
