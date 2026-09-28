import { fileURLToPath, URL } from 'node:url';

import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  base: '/',
  build: {
    // The CSP (spec 9) allows no inline scripts; Vite's modulepreload polyfill
    // injects one into index.html by default.
    modulePreload: { polyfill: false },
  },
  plugins: [
    vue({
      template: {
        compilerOptions: {
          isCustomElement: (tag) => tag.startsWith('nldd-'),
        },
      },
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // Runtime-only build: templates are already compiled by @vitejs/plugin-vue,
      // so the runtime compiler (and the unsafe-eval it needs) is never required.
      vue: 'vue/dist/vue.runtime.esm-bundler.js',
    },
  },
  test: {
    environment: 'jsdom',
    // https, otherwise jsdom refuses the __Host- cookie (needs a secure context)
    environmentOptions: { jsdom: { url: 'https://plak.test/' } },
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['tests/**/*.test.ts', 'src/**/*.test.ts'],
    coverage: {
      // Every line and branch is tested or carries a v8 ignore with its
      // reason (CLAUDE.md); anything below 100 fails the run. Only
      // `/* v8 ignore start */ ... /* v8 ignore stop */` works in this
      // setup, `next` and `if` are silently ignored.
      thresholds: {
        statements: 100,
        branches: 100,
        functions: 100,
        lines: 100,
      },
    },
  },
});
