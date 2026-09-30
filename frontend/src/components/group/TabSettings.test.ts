import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import type { Access, Site } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import { fireDetailEvent, serverErrorFetch } from '@/components/site/testHelpers';
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

function makeWrapper(props: { access?: Access; sites?: Site[]; canDelete?: boolean } = {}) {
  return mount(TabSettings, {
    props: {
      group: 'team-aurora',
      access: props.access ?? access,
      sites: props.sites ?? [],
      canDelete: props.canDelete ?? false,
    },
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

describe('group TabSettings: danger zone', () => {
  function site(slug: string, title = slug): Site {
    return { groupSlug: 'team-aurora', slug, title } as Site;
  }

  function typeSlug(wrapper: ReturnType<typeof makeWrapper>, value = 'team-aurora'): void {
    fireDetailEvent(wrapper.find('[data-testid="bevestig-zin"]').element, 'input', { value });
  }

  async function confirmDeletion(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
    await wrapper.find('[data-testid="verwijder-groep"]').trigger('click');
    typeSlug(wrapper);
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await flushPromises();
  }

  it('shows nothing of it to someone who may not delete the group', () => {
    const wrapper = makeWrapper({ canDelete: false });

    expect(wrapper.find('nldd-box[background="critical"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="verwijder-groep"]').exists()).toBe(false);
    expect(wrapper.findComponent(ConfirmModal).exists()).toBe(false);
  });

  it('offers the button while the group still has sites', () => {
    const wrapper = makeWrapper({ canDelete: true, sites: [site('website')] });

    expect(wrapper.find('[data-testid="verwijder-groep"]').exists()).toBe(true);
  });

  it('names the sites that go along in the dialog', async () => {
    const wrapper = makeWrapper({ canDelete: true, sites: [site('website', 'Website')] });

    await wrapper.find('[data-testid="verwijder-groep"]').trigger('click');

    expect(wrapper.findComponent(ConfirmModal).props('text')).toContain('deze sites');
    const rows = wrapper.findAll('[data-testid="groep-sites-lijst"] nldd-text-cell');
    expect(rows).toHaveLength(1);
    expect(rows[0]!.attributes('text')).toBe('Website');
    expect(rows[0]!.attributes('supporting-text')).toBe('team-aurora/website');
    expect(wrapper.find('[data-testid="groep-sites-rest"]').exists()).toBe(false);
  });

  it('names five sites and counts the rest', async () => {
    const sites = ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((slug) => site(slug));
    const wrapper = makeWrapper({ canDelete: true, sites });

    await wrapper.find('[data-testid="verwijder-groep"]').trigger('click');

    expect(wrapper.findAll('[data-testid="groep-sites-lijst"] nldd-list-item')).toHaveLength(6);
    expect(
      wrapper.find('[data-testid="groep-sites-rest"] nldd-text-cell').attributes('text'),
    ).toBe('En nog 2 sites');
  });

  it('says "1 site" when a single one is left unnamed', async () => {
    const sites = ['a', 'b', 'c', 'd', 'e', 'f'].map((slug) => site(slug));
    const wrapper = makeWrapper({ canDelete: true, sites });

    await wrapper.find('[data-testid="verwijder-groep"]').trigger('click');

    expect(
      wrapper.find('[data-testid="groep-sites-rest"] nldd-text-cell').attributes('text'),
    ).toBe('En nog 1 site');
  });

  it('lists nothing for an empty group and says only the group goes', async () => {
    const wrapper = makeWrapper({ canDelete: true });

    await wrapper.find('[data-testid="verwijder-groep"]').trigger('click');

    expect(wrapper.find('[data-testid="groep-sites-lijst"]').exists()).toBe(false);
    expect(wrapper.findComponent(ConfirmModal).props('text')).not.toContain('deze sites');
  });

  it('deletes the group with its sites only after its slug is typed', async () => {
    const wrapper = makeWrapper({ canDelete: true, sites: [site('website')] });

    await wrapper.find('[data-testid="verwijder-groep"]').trigger('click');
    expect(wrapper.findComponent(ConfirmModal).props('confirmPhrase')).toBe('team-aurora');
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await flushPromises();
    expect(backend.data.groups.map((g) => g.slug)).toContain('team-aurora');
    expect(wrapper.emitted('removed')).toBeUndefined();

    typeSlug(wrapper);
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await flushPromises();

    expect(backend.data.groups.map((g) => g.slug)).not.toContain('team-aurora');
    expect(backend.data.sites.filter((s) => s.groupSlug === 'team-aurora')).toHaveLength(0);
    expect(wrapper.emitted('removed')).toHaveLength(1);
    expect(wrapper.findComponent(ConfirmModal).props('open')).toBe(false);
  });

  it('Behoud groep closes the dialog without deleting', async () => {
    const wrapper = makeWrapper({ canDelete: true });

    await wrapper.find('[data-testid="verwijder-groep"]').trigger('click');
    expect(wrapper.find('[data-testid="bevestig-annuleren"]').attributes('text')).toBe(
      'Behoud groep',
    );
    await wrapper.find('[data-testid="bevestig-annuleren"]').trigger('click');
    await flushPromises();

    expect(wrapper.findComponent(ConfirmModal).props('open')).toBe(false);
    expect(backend.data.groups.map((g) => g.slug)).toContain('team-aurora');
  });

  it('closes the dialog and reports the refusal from the server', async () => {
    // The role was taken away in the meantime: the server has the last word.
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        new Response(
          JSON.stringify({
            type: 'about:blank',
            title: 'Geen toegang',
            status: 403,
            detail: 'Je rol in deze groep is te smal.',
          }),
          { status: 403, headers: { 'content-type': 'application/problem+json' } },
        ),
      ),
    );
    const wrapper = makeWrapper({ canDelete: true });

    await confirmDeletion(wrapper);

    expect(wrapper.findComponent(ConfirmModal).props('open')).toBe(false);
    const notice = wrapper.find('nldd-notification[text="Groep niet verwijderd"]');
    expect(notice.attributes('variant')).toBe('critical');
    expect(notice.attributes('supporting-text')).toBe('Je rol in deze groep is te smal.');
    expect(wrapper.emitted('removed')).toBeUndefined();
    expect(wrapper.find('[data-testid="bevestig-doorgaan"]').attributes('loading')).toBeUndefined();
  });

  it('reports the title of a problem without a detail', async () => {
    const wrapper = makeWrapper({ canDelete: true });
    vi.stubGlobal('fetch', serverErrorFetch());

    await confirmDeletion(wrapper);

    expect(
      wrapper.find('nldd-notification[text="Groep niet verwijderd"]').attributes('supporting-text'),
    ).toBe('Serverfout');
  });

  it('reports a generic failure when deleting throws something other than an ApiError', async () => {
    const wrapper = makeWrapper({ canDelete: true });
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    await confirmDeletion(wrapper);

    expect(
      wrapper.find('nldd-notification[text="Groep niet verwijderd"]').attributes('supporting-text'),
    ).toBe('Verwijderen is niet gelukt.');
  });
});
