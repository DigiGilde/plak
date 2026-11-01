import '@nldd/design-system';
// The open-font build (Inter): RijksSans is licensed for Rijksoverheid publications
// only, and anyone may run Plak.
import '@nldd/design-system/styles/open-font';
import './global.css';

import { createApp } from 'vue';

import App from './App.vue';
import { useDesignSystemText } from './i18n/designSystem';
import router from './router';

function start(): void {
  // Before the first mount, so no component is ever rendered in Dutch first.
  useDesignSystemText();

  const app = createApp(App);
  app.use(router);
  app.mount('#app');
}

start();
