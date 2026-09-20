declare module '*.vue' {
  import type { DefineComponent } from 'vue';
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component: DefineComponent<Record<string, any>, Record<string, any>, unknown>;
  export default component;
}

// Generated template types for every nldd-* element: props carry the
// components' own types ('md' | 'sm' instead of string). isCustomElement in
// vite.config.ts is still needed for the runtime compiler.
import '@nldd/design-system/vue';

export {};
