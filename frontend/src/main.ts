import '@nldd/design-system';
// The system-font build: RijksSans is licensed for Rijksoverheid publications
// only, and anyone may run Plak.
import '@nldd/design-system/styles/system-font';
import './global.css';

import { createApp } from 'vue';

import App from './App.vue';
import { useDesignSystemText } from './i18n/designSystem';
import router from './router';

async function start(): Promise<void> {
  if (import.meta.env.VITE_MOCK_API === 'true') {
    const { installMock } = await import('./api/mock');
    installMock();
  }

  // Before the first mount, so no component is ever rendered in Dutch first.
  useDesignSystemText();

  const app = createApp(App);
  app.use(router);
  app.mount('#app');
}

void start();
