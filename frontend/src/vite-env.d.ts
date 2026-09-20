/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "true" enables the in-memory mock backend (src/api/mock.ts) as long as the real backend is not there yet. */
  readonly VITE_MOCK_API?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
