import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import { serverErrorFetch, untilIdle } from '@/components/site/testHelpers';
import { _resetBreadcrumbs, breadcrumbsFor } from '@/composables/breadcrumbs';
import Apparaten from './Apparaten.vue';

let backend: MockBackend;
let router: Router;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  _resetBreadcrumbs();
  router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/-/devices', component: Apparaten }],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function makeWrapper() {
  await router.push('/-/devices');
  await router.isReady();
  return mount(Apparaten, { global: { plugins: [router], stubs: { teleport: true } } });
}

describe('Apparaten: overview', () => {
  it('shows the seeded device with the three times', async () => {
    const wrapper = await makeWrapper();
    await untilIdle();

    const row = wrapper.find('[data-testid="apparaat-cli-sessie-1"]');
    expect(row.exists()).toBe(true);
    expect(row.html()).toContain('plak-cli');
    expect(row.html()).toContain('Gekoppeld');
    expect(row.html()).toContain('laatst gebruikt');
    expect(row.html()).toContain('verloopt');
  });

  it('shows "nog niet" when the device has never been used', async () => {
    backend.data.cliSessions[0]!.lastUsedAt = null;

    const wrapper = await makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="apparaat-cli-sessie-1"]').html()).toContain('nog niet');
  });

  it('shows "Onbekend programma" without clientName', async () => {
    backend.data.cliSessions[0]!.clientName = null;

    const wrapper = await makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="apparaat-cli-sessie-1"]').html()).toContain(
      'Onbekend programma',
    );
  });

  it('explains plak login in the empty state', async () => {
    backend.data.cliSessions = [];

    const wrapper = await makeWrapper();
    await untilIdle();

    // The text goes in the component's own supporting-text rather than a
    // slotted paragraph: the slot sits outside the layout that centres the
    // icon and the heading, which left the sentence hanging beside them.
    const empty = wrapper.find('[data-testid="apparaten-leeg"]');
    expect(empty.exists()).toBe(true);
    expect(empty.attributes('supporting-text')).toContain('plak login');
    expect(empty.attributes('supporting-text')).toContain(
      'de Plak-CLI te koppelen aan je account',
    );
  });

  it('provides the breadcrumb path to the app shell', async () => {
    await makeWrapper();
    await untilIdle();

    expect(breadcrumbsFor('/-/devices')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Gekoppelde apparaten' },
    ]);
  });

  it('shows an error message on a server error', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());

    const wrapper = await makeWrapper();
    await untilIdle();

    expect(wrapper.html()).toContain('Serverfout');
  });
});

describe('Apparaten: disconnecting', () => {
  it('asks for confirmation and only then removes the device', async () => {
    const wrapper = await makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="apparaat-ontkoppelen-cli-sessie-1"]').trigger('click');
    await untilIdle();

    expect(backend.data.cliSessions).toHaveLength(1);

    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await untilIdle();

    expect(backend.data.cliSessions).toHaveLength(0);
    expect(wrapper.find('[data-testid="apparaat-cli-sessie-1"]').exists()).toBe(false);
  });

  it('reports it when disconnecting fails and keeps the row', async () => {
    const wrapper = await makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="apparaat-ontkoppelen-cli-sessie-1"]').trigger('click');
    vi.stubGlobal('fetch', serverErrorFetch());
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('nldd-notification[variant="critical"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="apparaat-cli-sessie-1"]').exists()).toBe(true);
  });
});
