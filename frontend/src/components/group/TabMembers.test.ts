import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import { SEARCH_DEBOUNCE_MS } from '@/composables/memberSearch';
import TabMembers from './TabMembers.vue';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeWrapper() {
  return mount(TabMembers, {
    props: {
      group: 'team-aurora',
      members: backend.data.groupMembers
        .filter((row) => row.groupSlug === 'team-aurora')
        .map((row) => ({ ...row, siteRoles: [] })),
    },
    global: { stubs: { teleport: true } },
  });
}

describe('group TabMembers', () => {
  it('searches the group through the real api.searchGroupMembers', async () => {
    const wrapper = makeWrapper();

    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] });
    try {
      wrapper
        .find('nldd-combo-box[name="identifier"]')
        .element.dispatchEvent(new CustomEvent('input', { detail: { value: 'Sanne' } }));
      vi.advanceTimersByTime(SEARCH_DEBOUNCE_MS);
      await flushPromises();

      expect(
        wrapper
          .find('[data-testid="lid-suggesties"]')
          .findAll('nldd-menu-item')
          .map((item) => item.attributes('value')),
      ).toContain('sanne@voorbeeld.nl');
    } finally {
      vi.useRealTimers();
    }
  });

  it('adds a member through the real api.addGroupMember, with an optimistic row', async () => {
    const wrapper = makeWrapper();

    wrapper
      .find('nldd-combo-box[name="identifier"]')
      .element.dispatchEvent(new CustomEvent('change', { detail: { value: 'sanne@voorbeeld.nl' } }));
    await wrapper.find('[data-testid="lid-formulier"]').trigger('submit');
    await flushPromises();

    expect(wrapper.emitted('memberAdded')?.[0]).toBeTruthy();
    expect(backend.data.groupMembers.map((l) => l.identifier)).toContain('sanne@voorbeeld.nl');
  });

  it('changes a role through the real api.setGroupRole', async () => {
    const wrapper = makeWrapper();

    wrapper
      .find('[data-testid="lid-rol-ada@voorbeeld.nl-admin"]')
      .element.dispatchEvent(new CustomEvent('select'));
    await flushPromises();

    expect(wrapper.emitted('memberRoleChanged')?.[0]).toBeTruthy();
    expect(backend.data.groupMembers.find((l) => l.identifier === 'ada@voorbeeld.nl')?.role).toBe(
      'admin',
    );
  });

  it('removes a member through the real api.removeGroupMember', async () => {
    const wrapper = makeWrapper();

    wrapper
      .find('[data-testid="lid-verwijderen-ada@voorbeeld.nl"]')
      .element.dispatchEvent(new CustomEvent('select'));
    await flushPromises();
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await flushPromises();

    expect(wrapper.emitted('memberRemoved')?.[0]).toEqual(['ada@voorbeeld.nl']);
    expect(backend.data.groupMembers.map((l) => l.identifier)).not.toContain('ada@voorbeeld.nl');
  });
});
