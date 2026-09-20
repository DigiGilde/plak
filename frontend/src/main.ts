import '@nldd/design-system';
import '@nldd/design-system/styles';
import './global.css';

import { VueQueryPlugin } from '@tanstack/vue-query';
import { createApp } from 'vue';

import App from './App.vue';
import router from './router';

async function start(): Promise<void> {
  if (import.meta.env.VITE_MOCK_API === 'true') {
    const { installMock } = await import('./api/mock');
    installMock();
  }

  const app = createApp(App);
  app.use(router);
  app.use(VueQueryPlugin);
  app.mount('#app');
}

void start();
