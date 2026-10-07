/// <reference types="vite/client" />

// The package export resolves to a .css file, but vite/client's '*.css'
// declaration matches on the specifier, which has no extension.
declare module '@nldd/design-system/styles/system-font';

interface ImportMetaEnv {
  /** "true" enables the in-memory mock backend (src/api/mock.ts) as long as the real backend is not there yet. */
  readonly VITE_MOCK_API?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
