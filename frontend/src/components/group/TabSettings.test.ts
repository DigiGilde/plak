import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import type { Access } from '@/api/types';
import TabSettings from './TabSettings.vue';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const access: Access = { base: 'public', keys: false, invitees: false };

function makeWrapper(props: { access?: Access } = {}) {
  return mount(TabSettings, {
    props: { group: 'team-aurora', access: props.access ?? access },
    global: { stubs: { teleport: true } },
  });
}

function fireChange(el: Element, checked: boolean): void {
  el.dispatchEvent(new CustomEvent('change', { detail: { checked } }));
}

describe('group TabSettings', () => {
  it('saves a chosen base through the real api.setGroupDefaultAccess', async () => {
    const wrapper = makeWrapper();

    await wrapper.find('[data-testid="standaardtoegang-site_team"]').trigger('change');
    await flushPromises();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual({
      base: 'site_team',
      keys: false,
      invitees: false,
    });
    expect(wrapper.emitted('groupChanged')).toBeTruthy();
  });

  it('leaves the already chosen base alone, without calling the API', async () => {
    const wrapper = makeWrapper();

    await wrapper.find('[data-testid="standaardtoegang-public"]').trigger('change');
    await flushPromises();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual(access);
    expect(wrapper.emitted('groupChanged')).toBeUndefined();
  });

  it('turns on the keys exception', async () => {
    const wrapper = makeWrapper();

    fireChange(wrapper.find('[data-testid="standaardtoegang-sleutels"]').element, true);
    await flushPromises();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual({
      base: 'public',
      keys: true,
      invitees: false,
    });
  });

  it('turns on the invitees exception', async () => {
    const wrapper = makeWrapper();

    fireChange(wrapper.find('[data-testid="standaardtoegang-genodigden"]').element, true);
    await flushPromises();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual({
      base: 'public',
      keys: false,
      invitees: true,
    });
  });

  it('leaves an extra untouched when switched to its own current value', async () => {
    const wrapper = makeWrapper();

    fireChange(wrapper.find('[data-testid="standaardtoegang-sleutels"]').element, false);
    await flushPromises();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual(access);
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('rolls back and reports a server failure', async () => {
    const wrapper = makeWrapper();

    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        new Response(
          JSON.stringify({ type: 'about:blank', title: 'Serverfout', status: 500 }),
          { status: 500, headers: { 'content-type': 'application/problem+json' } },
        ),
      ),
    );
    await wrapper.find('[data-testid="standaardtoegang-site_team"]').trigger('change');
    await flushPromises();

    expect(
      wrapper.find('[data-testid="standaardtoegang-public"]').attributes('checked'),
    ).toBeDefined();
    expect(
      wrapper.find('nldd-notification[text="Standaardtoegang niet opgeslagen"]').attributes(
        'supporting-text',
      ),
    ).toBe('Serverfout');
  });

  it('reports a generic failure when saving throws something other than an ApiError', async () => {
    const wrapper = makeWrapper();

    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));
    await wrapper.find('[data-testid="standaardtoegang-site_team"]').trigger('change');
    await flushPromises();

    expect(
      wrapper.find('nldd-notification[text="Standaardtoegang niet opgeslagen"]').attributes(
        'supporting-text',
      ),
    ).toBe('Opslaan is niet gelukt.');
  });
});
