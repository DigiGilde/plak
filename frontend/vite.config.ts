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
  },
});
