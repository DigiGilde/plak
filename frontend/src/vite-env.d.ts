/// <reference types="vite/client" />

// The package export resolves to a .css file, but vite/client's '*.css'
// declaration matches on the specifier, which has no extension.
declare module '@nldd/design-system/styles/system-font';
