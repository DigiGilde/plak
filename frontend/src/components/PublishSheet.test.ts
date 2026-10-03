import { flushPromises, mount } from '@vue/test-utils';
import type { Mock } from 'vitest';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/client';
import type { Group, Me, MyGroupRole, Site } from '@/api/types';

import PublishSheet from './PublishSheet.vue';

const AURORA: Group = { slug: 'team-aurora', name: 'Team Aurora', defaultAccess: { base: 'public', keys: false, invitees: false } };
const TEAM: Group = { slug: 'team', name: 'Team', defaultAccess: { base: 'site_team', keys: false, invitees: false } };
const FRESH: Group = { slug: 'mijn-team', name: 'Mijn team', defaultAccess: { base: 'site_team', keys: false, invitees: false } };

/** A member with the given group roles; only `groupRoles` varies per test. */
function meWith(groupRoles: MyGroupRole[]): Me {
  return {
    id: 'lid-1',
    ssoSubject: 'lid-1',
    email: 'lid@voorbeeld.nl',
    name: 'Lid',
    platformRole: 'member',
    status: 'active',
    createdAt: '2026-01-01T00:00:00Z',
    lastLoginAt: null,
    contentBaseUrl: 'https://sites.plak.test',
    groupRoles,
    siteRoles: [],
    ciForgejoHosts: [],
    ciAudience: 'https://plak.test',
    language: null,
  };
}

const site: Site = {
  groupSlug: 'team-aurora',
  slug: 'mijn-site',
  title: 'Mijn site',
  access: { base: 'public', keys: false, invitees: false },
  externalSources: false,
  sandbox: true,
  liveVersionsKept: null,
  liveVersionId: null,
  createdBy: 'dev-beheerder',
  hasLiveVersion: false,
  lastPublishedAt: null,
  previewCount: 0,
};

type Wrapper = ReturnType<typeof mount>;

// `Mock` and not `ReturnType<typeof vi.fn>`: since vitest 4 that ReturnType
// resolves the generic to its constraint `Procedure | Constructable`, which no
// longer satisfies a plain call signature, so the props would not typecheck.
interface Flow {
  createGroup: Mock;
  createSite: Mock;
  setAccess: Mock;
  publish: Mock;
}

function flow(override: Partial<Flow> = {}): Flow {
  return {
    createGroup: vi.fn().mockResolvedValue(FRESH),
    createSite: vi.fn().mockResolvedValue(site),
    setAccess: vi.fn().mockImplementation((_g, _s, visibility) =>
      Promise.resolve({ ...site, visibility }),
    ),
    publish: vi.fn().mockResolvedValue(undefined),
    ...override,
  } as Flow;
}

function mountComponent(
  options: { groups?: Group[]; me?: Me | null; contentBase?: string; open?: boolean } & Partial<Flow> = {},
): { wrapper: Wrapper; actions: Flow } {
  const { groups = [AURORA], me = null, contentBase = 'https://sites.plak.test', open = true, ...rest } =
    options;
  const actions = flow(rest);
  const wrapper = mount(PublishSheet, {
    props: { open, groups, me, contentBase, ...actions },
    global: { stubs: { teleport: true } },
  });
  return { wrapper, actions };
}

function typeIn(wrapper: Wrapper, name: string, value: string): void {
  const el = wrapper.find(`nldd-text-field[name="${name}"]`).element as HTMLElement;
  el.dispatchEvent(new CustomEvent('input', { detail: { value: value } }));
}

/** As a real <input> reports it: no detail, the value sits on the field. */
function typeNative(wrapper: Wrapper, name: string, value: string): void {
  const el = wrapper.find(`nldd-text-field[name="${name}"]`).element as HTMLElement & {
    value?: string;
  };
  el.value = value;
  el.dispatchEvent(new Event('input'));
}

function choose(wrapper: Wrapper, ...files: File[]): void {
  const el = wrapper.find('nldd-file-field').element as HTMLElement;
  el.dispatchEvent(new CustomEvent('change', { detail: { files: files } }));
}

function file(name: string): File {
  return new File(['<h1>hoi</h1>'], name, { type: 'text/html' });
}

/** Big enough to exercise the kB/MB step-up in the size shown beside the chip. */
function bigFile(name: string, bytes: number): File {
  return new File([new Uint8Array(bytes)], name, { type: 'application/zip' });
}

async function submit(wrapper: Wrapper): Promise<void> {
  await wrapper.find('nldd-form').trigger('submit');
  await flushPromises();
}

function textOf(wrapper: Wrapper, testid: string): string {
  return wrapper.find(`[data-testid="${testid}"]`).text();
}

/** The summary sits in an attribute, the way nldd-inline-dialog takes it. */
function explanationOf(wrapper: Wrapper): string {
  return wrapper.find('[data-testid="publish-access-explanation"]').attributes('text') ?? '';
}

describe('PublishSheet (the file drives the rest)', () => {
  it('derives title and address from the file name, so the normal path costs no typing', async () => {
    const { wrapper } = mountComponent();

    choose(wrapper, file('mijn-site.zip'));
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe('Mijn site');
    expect(textOf(wrapper, 'publish-address')).toContain(
      'https://sites.plak.test/team-aurora/mijn-site/',
    );
  });

  it.each([
    ['kwartaal_rapportage.tar.gz', 'Kwartaal rapportage'],
    ['site.tgz', 'Site'],
    ['Plan.HTML', 'Plan'],
    ['losse-pagina.htm', 'Losse pagina'],
  ])('strips the extension from %s and turns it into "%s"', async (name, expect_) => {
    const { wrapper } = mountComponent();

    choose(wrapper, file(name));
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe(expect_);
  });

  it.each(['index.html', 'index.htm', '.zip'])(
    'leaves the title empty for %s, since that is the name of the mechanism',
    async (name) => {
      const { wrapper } = mountComponent();

      choose(wrapper, file(name));
      await flushPromises();

      expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe('');
      expect(wrapper.find('[data-testid="publish-address-empty"]').exists()).toBe(true);
    },
  );

  it('keeps a title the user typed themselves on a second file choice', async () => {
    const { wrapper } = mountComponent();

    typeIn(wrapper, 'title', 'Eigen titel');
    choose(wrapper, file('heel-iets-anders.zip'));
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe('Eigen titel');
  });

  it('does nothing when the file field is cleared, instead of wiping the title', async () => {
    const { wrapper } = mountComponent();

    choose(wrapper, file('mijn-site.zip'));
    await flushPromises();
    choose(wrapper);
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe('Mijn site');
  });

  it('also reads the choice from a change event without detail (native input)', async () => {
    const { wrapper } = mountComponent();
    const field = wrapper.find('nldd-file-field').element as unknown as HTMLElement;
    Object.defineProperty(field, 'files', { value: [file('rapport.zip')], configurable: true });

    field.dispatchEvent(new Event('change'));
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe('Rapport');
  });

  it('lets the field itself show the name on a choice via the field, without a second notice', async () => {
    const { wrapper } = mountComponent();

    expect(wrapper.find('[data-testid="publish-file-chosen"]').exists()).toBe(false);

    choose(wrapper, file('mijn-site.zip'));
    await flushPromises();

    expect(wrapper.find('[data-testid="publish-file-chosen"]').exists()).toBe(false);
    const field = wrapper.find('[data-testid="publish-file"]');
    expect(field.attributes('required')).toBeDefined();
    expect(field.attributes('invalid')).toBeUndefined();
  });

  it('says upfront what is allowed, instead of only after a failed attempt', () => {
    const { wrapper } = mountComponent();

    const field = wrapper.find('nldd-form-field[label="Bestand"]');
    expect(field.attributes('supporting-label')).toContain('.zip');
    expect(field.attributes('supporting-label')).toContain('HTML-bestand');
  });
});

describe('PublishSheet (the address is always visible and editable)', () => {
  it('shows the address field right away, prefilled from the title', async () => {
    const { wrapper } = mountComponent();

    typeIn(wrapper, 'title', 'Mijn Nieuwe Site!');
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="slug"]').attributes('value')).toBe(
      'mijn-nieuwe-site',
    );
    expect(textOf(wrapper, 'publish-address')).toContain(
      'https://sites.plak.test/team-aurora/mijn-nieuwe-site/',
    );
  });

  it('lets the title release the address as soon as the address is edited by hand', async () => {
    const { wrapper } = mountComponent();

    typeIn(wrapper, 'title', 'Eerste');
    await flushPromises();
    expect(wrapper.find('nldd-text-field[name="slug"]').attributes('value')).toBe('eerste');

    typeIn(wrapper, 'slug', 'eigen-adres');
    typeIn(wrapper, 'title', 'Tweede');
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="slug"]').attributes('value')).toBe('eigen-adres');
    expect(textOf(wrapper, 'publish-address')).toContain('/team-aurora/eigen-adres/');
  });

  it('follows the title again once the address is cleared', async () => {
    const { wrapper } = mountComponent();

    typeIn(wrapper, 'title', 'Eerste');
    await flushPromises();
    typeIn(wrapper, 'slug', 'eigen-adres');
    await flushPromises();

    typeIn(wrapper, 'slug', '');
    typeIn(wrapper, 'title', 'Tweede');
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="slug"]').attributes('value')).toBe('tweede');
  });

  it('normalizes the address while typing, instead of only complaining on submit', async () => {
    const { wrapper, actions } = mountComponent();

    // Character by character, the way a field reports it: the value that comes
    // back goes in again, so nothing may be reordered or swallowed.
    let shown = '';
    for (const character of 'Mijn Rapport 2026') {
      typeIn(wrapper, 'slug', shown + character);
      await flushPromises();
      shown = wrapper.find('nldd-text-field[name="slug"]').attributes('value')!;
    }

    expect(shown).toBe('mijn-rapport-2026');

    choose(wrapper, file('iets.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(actions.createSite).toHaveBeenCalledWith('team-aurora', 'Iets', 'mijn-rapport-2026');
  });

  it('shows the path without a content origin, not a half address with an empty host', async () => {
    const { wrapper } = mountComponent({ contentBase: '' });

    typeIn(wrapper, 'title', 'Site');
    await flushPromises();

    expect(textOf(wrapper, 'publish-address')).toContain('/team-aurora/site/');
  });
});

describe('PublishSheet (input without a detail event)', () => {
  it('also reads title, group name and address from the field itself', async () => {
    const { wrapper, actions } = mountComponent({ groups: [] });

    typeNative(wrapper, 'group-name', 'Mijn team');
    typeNative(wrapper, 'title', 'Mijn site');
    await flushPromises();
    typeNative(wrapper, 'slug', 'eigen-adres');
    choose(wrapper, file('iets.zip'));
    await flushPromises();

    expect(textOf(wrapper, 'publish-address')).toContain('/mijn-team/eigen-adres/');

    await submit(wrapper);

    expect(actions.createGroup).toHaveBeenCalledWith('Mijn team', 'mijn-team');
    expect(actions.createSite).toHaveBeenCalledWith('mijn-team', 'Mijn site', 'eigen-adres');
  });
});

describe('PublishSheet (the group)', () => {
  it('asks nothing with exactly one group', () => {
    const { wrapper } = mountComponent({ groups: [AURORA] });

    expect(wrapper.find('nldd-dropdown').exists()).toBe(false);
    expect(wrapper.find('nldd-text-field[name="group-name"]').exists()).toBe(false);
  });

  it('offers the choice with more than one group and lets the address move along', async () => {
    const { wrapper, actions } = mountComponent({ groups: [AURORA, TEAM] });

    const select = wrapper.find('nldd-dropdown select');
    expect(select.findAll('option').map((o) => o.attributes('value'))).toEqual(['team-aurora', 'team']);
    expect((select.element as HTMLSelectElement).value).toBe('team-aurora');

    (select.element as HTMLSelectElement).value = 'team';
    await select.trigger('change');
    choose(wrapper, file('handboek.zip'));
    await flushPromises();

    expect(textOf(wrapper, 'publish-address')).toContain('/team/handboek/');
    // The chosen group also decides the base that is preselected.
    expect(
      wrapper.find('[data-testid="publish-visibility-site_team"]').attributes('checked'),
    ).toBeDefined();

    await submit(wrapper);
    expect(actions.createSite).toHaveBeenCalledWith('team', 'Handboek', 'handboek');
    expect(actions.createGroup).not.toHaveBeenCalled();
  });

  it('picks another group as soon as the running list no longer contains the current choice', async () => {
    const { wrapper } = mountComponent({ groups: [] });

    await wrapper.setProps({ groups: [TEAM] });
    choose(wrapper, file('site.zip'));
    await flushPromises();

    expect(textOf(wrapper, 'publish-address')).toContain('/team/site/');
  });

  it('keeps the chosen group when the running list still contains it', async () => {
    const { wrapper } = mountComponent({ groups: [AURORA, TEAM] });

    const select = wrapper.find('nldd-dropdown select');
    (select.element as HTMLSelectElement).value = 'team';
    await select.trigger('change');

    await wrapper.setProps({ groups: [AURORA, TEAM, FRESH] });
    choose(wrapper, file('site.zip'));
    await flushPromises();

    expect(textOf(wrapper, 'publish-address')).toContain('/team/site/');
  });

  it('does not let the flow strand without a group, but creates one', async () => {
    const { wrapper, actions } = mountComponent({ groups: [] });

    expect(wrapper.find('nldd-dropdown').exists()).toBe(false);
    const field = wrapper.find('nldd-form-field[label="Naam van je groep"]');
    expect(field.exists()).toBe(true);
    expect(field.attributes('supporting-label')).toContain('maak je nu aan');
    // In the backend a fresh group starts on site_team; that belongs on screen
    // before anything is published.
    expect(
      wrapper.find('[data-testid="publish-visibility-site_team"]').attributes('checked'),
    ).toBeDefined();

    typeIn(wrapper, 'group-name', 'Mijn team');
    choose(wrapper, file('mijn-site.zip'));
    await flushPromises();

    expect(textOf(wrapper, 'publish-address')).toContain('/mijn-team/mijn-site/');

    await submit(wrapper);

    expect(actions.createGroup).toHaveBeenCalledWith('Mijn team', 'mijn-team');
    expect(actions.createSite).toHaveBeenCalledWith('mijn-team', 'Mijn site', 'mijn-site');
    expect(wrapper.emitted('groupCreated')?.[0]).toEqual([FRESH]);
  });
});

describe('PublishSheet (the group, filtered by role)', () => {
  const READERS: Group = { slug: 'lezers', name: 'Lezers', defaultAccess: { base: 'public', keys: false, invitees: false } };

  it('asks nothing when the user may only publish in one of the groups', () => {
    const me = meWith([
      { groupSlug: 'team-aurora', role: 'editor' },
      { groupSlug: 'lezers', role: 'reader' },
    ]);
    const { wrapper } = mountComponent({ groups: [AURORA, READERS], me });

    expect(wrapper.find('nldd-dropdown').exists()).toBe(false);
    expect(wrapper.find('nldd-text-field[name="group-name"]').exists()).toBe(false);
  });

  it('offers in the choice list only the groups where editor or admin applies', async () => {
    const me = meWith([
      { groupSlug: 'team-aurora', role: 'editor' },
      { groupSlug: 'team', role: 'admin' },
      { groupSlug: 'lezers', role: 'reader' },
    ]);
    const { wrapper, actions } = mountComponent({ groups: [AURORA, TEAM, READERS], me });

    const select = wrapper.find('nldd-dropdown select');
    expect(select.findAll('option').map((o) => o.attributes('value'))).toEqual(['team-aurora', 'team']);

    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(actions.createSite).toHaveBeenCalledWith('team-aurora', 'Site', 'site');
  });

  it('leaves no group without an editor or admin role in the choice list, not even for a platform admin', () => {
    const me = { ...meWith([]), platformRole: 'admin' as const };
    const { wrapper } = mountComponent({ groups: [AURORA, TEAM], me });

    // A platform admin gets no bypass here (backend `create_site` demands an
    // actual group role): with none, this is the "no eligible group" case.
    expect(wrapper.find('nldd-dropdown').exists()).toBe(false);
    expect(wrapper.find('nldd-form-field[label="Naam van je groep"]').exists()).toBe(true);
  });

  it('explains and offers a new group when groups exist but nowhere as editor or admin', async () => {
    const me = meWith([{ groupSlug: 'lezers', role: 'reader' }]);
    const { wrapper, actions } = mountComponent({ groups: [READERS], me });

    const field = wrapper.find('nldd-form-field[label="Naam van je groep"]');
    expect(field.exists()).toBe(true);
    expect(field.attributes('supporting-label')).toContain('editor of beheerder');

    typeIn(wrapper, 'group-name', 'Mijn team');
    choose(wrapper, file('mijn-site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(actions.createGroup).toHaveBeenCalledWith('Mijn team', 'mijn-team');
    expect(actions.createSite).toHaveBeenCalledWith('mijn-team', 'Mijn site', 'mijn-site');
  });

  it('keeps the regular notice when the user has no group at all', () => {
    const me = meWith([]);
    const { wrapper } = mountComponent({ groups: [], me });

    const field = wrapper.find('nldd-form-field[label="Naam van je groep"]');
    expect(field.attributes('supporting-label')).toContain('nog geen');
    expect(field.attributes('supporting-label')).not.toContain('editor of beheerder');
  });
});

describe('PublishSheet (publishing)', () => {
  it('does group, site and upload behind one button and reports the result', async () => {
    const { wrapper, actions } = mountComponent();
    const zip = file('mijn-site.zip');

    choose(wrapper, zip);
    await flushPromises();
    await submit(wrapper);

    expect(actions.createSite).toHaveBeenCalledWith('team-aurora', 'Mijn site', 'mijn-site');
    expect(actions.publish).toHaveBeenCalledWith('team-aurora', 'mijn-site', zip);
    expect(wrapper.emitted('created')?.[0]).toEqual([site]);
    expect(wrapper.emitted('published')?.[0]).toEqual([site]);
    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false]);
  });

  it('ignores a second click while the first is still running', async () => {
    let release!: () => void;
    const publish = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = () => resolve();
        }),
    );
    const { wrapper, actions } = mountComponent({ publish });

    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);
    expect(wrapper.find('[data-testid="publish-submit"]').attributes('loading')).toBeDefined();

    await submit(wrapper);
    release();
    await flushPromises();

    expect(actions.createSite).toHaveBeenCalledTimes(1);
    expect(actions.publish).toHaveBeenCalledTimes(1);
  });

  it('clears the form after success, so the next time starts clean', async () => {
    const { wrapper } = mountComponent();

    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe('');
    expect(wrapper.find('[data-testid="publish-address-empty"]').exists()).toBe(true);
  });

  it('leaves the default visibility alone when nothing else is chosen', async () => {
    const { wrapper, actions } = mountComponent({ groups: [AURORA] });

    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(actions.setAccess).not.toHaveBeenCalled();
  });

  it('sets a chosen base before the upload, so the first version is never more widely visible', async () => {
    const { wrapper, actions } = mountComponent({ groups: [AURORA] });
    const zip = file('site.zip');

    await wrapper.find('[data-testid="publish-visibility-site_team"]').trigger('change');
    choose(wrapper, zip);
    await flushPromises();
    await submit(wrapper);

    expect(actions.setAccess).toHaveBeenCalledWith('team-aurora', 'mijn-site', {
      base: 'site_team',
      keys: false,
      invitees: false,
    });
    const accessOrder = actions.setAccess.mock.invocationCallOrder[0];
    const publishOrder = actions.publish.mock.invocationCallOrder[0];
    expect(accessOrder).toBeLessThan(publishOrder);
  });

  it('stops before publishing when setting the chosen access fails', async () => {
    const setAccess = vi.fn().mockRejectedValue(
      new ApiError({ type: 'about:blank', title: 'Serverfout', status: 500 }),
    );
    const { wrapper, actions } = mountComponent({ groups: [AURORA], setAccess });

    await wrapper.find('[data-testid="publish-visibility-site_team"]').trigger('change');
    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(actions.publish).not.toHaveBeenCalled();
    expect(wrapper.find('nldd-banner').attributes('text')).toBe('Serverfout');
  });

  it('publishes with a secret link in one go, without a detour via the Toegang tab', async () => {
    const { wrapper, actions } = mountComponent({ groups: [AURORA] });

    await wrapper.find('[data-testid="publish-visibility-nobody"]').trigger('change');
    wrapper
      .find('[data-testid="publish-exception-keys"]')
      .element.dispatchEvent(new CustomEvent('change', { detail: { checked: true } }));
    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(actions.setAccess).toHaveBeenCalledWith('team-aurora', 'mijn-site', {
      base: 'nobody',
      keys: true,
      invitees: false,
    });
  });

  it('turns on invitees from the same sheet', async () => {
    const { wrapper, actions } = mountComponent({ groups: [AURORA] });

    await wrapper.find('[data-testid="publish-visibility-nobody"]').trigger('change');
    wrapper
      .find('[data-testid="publish-exception-invitees"]')
      .element.dispatchEvent(new CustomEvent('change', { detail: { checked: true } }));
    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(actions.setAccess).toHaveBeenCalledWith('team-aurora', 'mijn-site', {
      base: 'nobody',
      keys: false,
      invitees: true,
    });
  });

  it('tells at the choice who can actually see the site then', async () => {
    const { wrapper } = mountComponent({ groups: [AURORA] });

    await wrapper.find('[data-testid="publish-visibility-nobody"]').trigger('change');
    await flushPromises();
    expect(explanationOf(wrapper)).toContain('Niemand kan de site bekijken');

    wrapper
      .find('[data-testid="publish-exception-keys"]')
      .element.dispatchEvent(new CustomEvent('change', { detail: { checked: true } }));
    await flushPromises();

    const explanation = explanationOf(wrapper);
    expect(explanation).toContain('geheime link');
    expect(explanation).toContain('meteen na het publiceren');
  });

  it('starts from the group default access, exceptions included', async () => {
    const WITH_EXTRAS: Group = {
      slug: 'team-aurora',
      name: 'Team Aurora',
      defaultAccess: { base: 'nobody', keys: true, invitees: false },
    };
    const { wrapper } = mountComponent({ groups: [WITH_EXTRAS] });

    expect(
      wrapper.find('[data-testid="publish-exception-keys"]').attributes('checked'),
    ).toBeDefined();
    expect(
      wrapper.find('[data-testid="publish-exception-invitees"]').attributes('checked'),
    ).toBeUndefined();
  });

  it('also turns an exception to the group default back off', async () => {
    const WITH_EXTRAS: Group = {
      slug: 'team-aurora',
      name: 'Team Aurora',
      defaultAccess: { base: 'public', keys: true, invitees: false },
    };
    // The site is created on the group default, so turning the switch off is
    // a real change that has to reach the server.
    const createSite = vi
      .fn()
      .mockResolvedValue({ ...site, access: { base: 'public', keys: true, invitees: false } });
    const { wrapper, actions } = mountComponent({ groups: [WITH_EXTRAS], createSite });

    wrapper
      .find('[data-testid="publish-exception-keys"]')
      .element.dispatchEvent(new CustomEvent('change', { detail: { checked: false } }));
    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(actions.setAccess).toHaveBeenCalledWith('team-aurora', 'mijn-site', {
      base: 'public',
      keys: false,
      invitees: false,
    });
  });

  it('carries the exceptions of the group default along with a different base', async () => {
    // Untouched switches keep whatever the group default said.
    const WITH_KEYS: Group = {
      slug: 'team-aurora',
      name: 'Team Aurora',
      defaultAccess: { base: 'public', keys: true, invitees: false },
    };
    const { wrapper, actions } = mountComponent({ groups: [WITH_KEYS] });

    await wrapper.find('[data-testid="publish-visibility-sso"]').trigger('change');
    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(actions.setAccess).toHaveBeenCalledWith('team-aurora', 'mijn-site', {
      base: 'sso',
      keys: true,
      invitees: false,
    });
  });
});

describe('PublishSheet (refusals)', () => {
  it('flags a missing file without disabling the button', async () => {
    const { wrapper, actions } = mountComponent();

    expect(wrapper.find('[data-testid="publish-submit"]').attributes('disabled')).toBeUndefined();
    typeIn(wrapper, 'title', 'Site');
    await submit(wrapper);

    expect(actions.createSite).not.toHaveBeenCalled();
    expect(wrapper.find('nldd-file-field').attributes('invalid')).toBeDefined();
  });

  it('flags an empty title and releases the marker again once typing starts', async () => {
    const { wrapper, actions } = mountComponent();

    choose(wrapper, file('index.html'));
    await flushPromises();
    await submit(wrapper);

    expect(actions.createSite).not.toHaveBeenCalled();
    expect(wrapper.find('nldd-text-field[name="title"]').attributes('invalid')).toBeDefined();

    typeIn(wrapper, 'title', 'Welkom');
    await flushPromises();
    expect(wrapper.find('nldd-text-field[name="title"]').attributes('invalid')).toBeUndefined();
  });

  it('opens the address field for a title that yields no address, since the notice needs somewhere to attach', async () => {
    const { wrapper, actions } = mountComponent();

    choose(wrapper, file('site.zip'));
    await flushPromises();
    typeIn(wrapper, 'title', '???');
    await submit(wrapper);

    expect(actions.createSite).not.toHaveBeenCalled();
    const slugField = wrapper.find('nldd-text-field[name="slug"]');
    expect(slugField.exists()).toBe(true);
    expect(slugField.attributes('invalid')).toBeDefined();

    const shape = wrapper.find('nldd-validation-item#publish-slug-format');
    const match = new RegExp(shape.attributes('match')!, 'u');
    expect(match.test('Niet Geldig')).toBe(false);
    expect(match.test('-begin')).toBe(false);
    expect(match.test('mijn-site')).toBe(true);
    const pattern = new RegExp(`^(?:${slugField.attributes('pattern')!})$`, 'u');
    expect(pattern.test('Niet Geldig')).toBe(false);
    expect(pattern.test('mijn-site')).toBe(true);
  });

  it('asks for a group name that yields no address', async () => {
    const { wrapper, actions } = mountComponent({ groups: [] });

    choose(wrapper, file('site.zip'));
    await flushPromises();
    typeIn(wrapper, 'group-name', '!!!');
    await submit(wrapper);

    expect(actions.createGroup).not.toHaveBeenCalled();
    expect(wrapper.find('nldd-text-field[name="group-name"]').attributes('invalid')).toBeDefined();
  });

  it('shows a 409 on the address at the field, not as a banner', async () => {
    const createSite = vi.fn().mockRejectedValue(
      new ApiError({
        type: 'about:blank',
        title: 'Site bestaat al',
        status: 409,
        detail: 'Er bestaat al een site met slug "site" in deze groep.',
      }),
    );
    const { wrapper } = mountComponent({ createSite });

    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(wrapper.find('nldd-banner[variant="critical"]').exists()).toBe(false);
    const slugField = wrapper.find('nldd-text-field[name="slug"]');
    expect(slugField.attributes('unmet')).toBe('publish-slug-server');
    expect(wrapper.find('nldd-validation-item#publish-slug-server').text()).toContain(
      'bestaat al een site',
    );
    expect(wrapper.emitted('update:open')).toBeUndefined();
  });

  it('shows a 409 on the group name at that field', async () => {
    const createGroup = vi.fn().mockRejectedValue(
      new ApiError({
        type: 'about:blank',
        title: 'Groep bestaat al',
        status: 409,
        detail: 'Er bestaat al een groep met slug "mijn-team".',
      }),
    );
    const { wrapper, actions } = mountComponent({ groups: [], createGroup });

    choose(wrapper, file('site.zip'));
    await flushPromises();
    typeIn(wrapper, 'group-name', 'Mijn team');
    await submit(wrapper);

    expect(actions.createSite).not.toHaveBeenCalled();
    const field = wrapper.find('nldd-text-field[name="group-name"]');
    expect(field.attributes('unmet')).toBe('publish-group-server');
    expect(wrapper.find('nldd-validation-item#publish-group-server').text()).toContain(
      'bestaat al een groep',
    );
  });

  it('falls back to the problem title when there is no explanation', async () => {
    const createSite = vi.fn().mockRejectedValue(
      new ApiError({ type: 'about:blank', title: 'Ongeldige slug', status: 422 }),
    );
    const { wrapper } = mountComponent({ createSite });

    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(wrapper.find('nldd-validation-item#publish-slug-server').text()).toBe(
      'Ongeldige slug',
    );
  });

  it('shows an error that does not belong to a field as a banner', async () => {
    const createSite = vi.fn().mockRejectedValue(new Error('netwerk weg'));
    const { wrapper } = mountComponent({ createSite });

    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(wrapper.find('nldd-banner[variant="critical"]').attributes('text')).toBe(
      'Er ging iets mis',
    );
  });

  it('shows a group error that does not belong to a field as a banner', async () => {
    const createGroup = vi.fn().mockRejectedValue(new Error('netwerk weg'));
    const { wrapper } = mountComponent({ groups: [], createGroup });

    choose(wrapper, file('site.zip'));
    await flushPromises();
    typeIn(wrapper, 'group-name', 'Mijn team');
    await submit(wrapper);

    expect(wrapper.find('nldd-banner[variant="critical"]').attributes('text')).toBe(
      'Er ging iets mis',
    );
  });
});

describe('PublishSheet (getting stuck halfway)', () => {
  it('does not create the same site again after a failed upload', async () => {
    const publish = vi
      .fn()
      .mockRejectedValueOnce(new Error('upload mislukt'))
      .mockResolvedValueOnce(undefined);
    const { wrapper, actions } = mountComponent({ publish });

    choose(wrapper, file('site.zip'));
    await flushPromises();
    await submit(wrapper);

    expect(wrapper.find('[data-testid="publish-halfway"]').exists()).toBe(true);
    expect(wrapper.emitted('created')).toHaveLength(1);
    expect(wrapper.emitted('published')).toBeUndefined();

    await submit(wrapper);

    expect(actions.createSite).toHaveBeenCalledTimes(1);
    expect(actions.publish).toHaveBeenCalledTimes(2);
    expect(wrapper.emitted('published')?.[0]).toEqual([site]);
  });

  it('does not create the same group again after a failed site', async () => {
    const createSite = vi
      .fn()
      .mockRejectedValueOnce(new Error('even niet'))
      .mockResolvedValueOnce(site);
    const { wrapper, actions } = mountComponent({ groups: [], createSite });

    choose(wrapper, file('site.zip'));
    await flushPromises();
    typeIn(wrapper, 'group-name', 'Mijn team');
    await submit(wrapper);

    expect(actions.createGroup).toHaveBeenCalledTimes(1);
    // The group really exists now, so the question about it should be gone.
    expect(wrapper.find('nldd-text-field[name="group-name"]').exists()).toBe(false);

    await submit(wrapper);

    expect(actions.createGroup).toHaveBeenCalledTimes(1);
    expect(actions.createSite).toHaveBeenCalledTimes(2);
    expect(actions.createSite).toHaveBeenLastCalledWith('mijn-team', 'Site', 'site');
  });
});

describe('PublishSheet (closing and showing)', () => {
  it('closes with a button set apart from the primary action and discards the form', async () => {
    const { wrapper } = mountComponent();

    typeIn(wrapper, 'title', 'Halverwege');
    await wrapper.find('[data-testid="publish-close"]').trigger('click');

    expect(wrapper.emitted('update:open')?.at(-1)).toEqual([false]);
    expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe('');
    // The way out sits in the heading, not up against "Zet online".
    expect(wrapper.find('[data-testid="publish-close"]').attributes('slot')).toBe('end');
    expect(wrapper.find('nldd-form-actions nldd-button[type="button"]').exists()).toBe(false);
  });

  it('opens and closes the sheet itself via show and hide', async () => {
    // In this test nldd-sheet is an unknown element without those methods, and
    // a re-render produces a new element: so the spies belong on the prototype,
    // not on the instance of this moment.
    const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
    const show = vi.fn();
    const hide = vi.fn();
    proto.show = show;
    proto.hide = hide;
    try {
      const { wrapper } = mountComponent({ open: false });
      expect(show).not.toHaveBeenCalled();

      await wrapper.setProps({ open: true });
      await flushPromises();
      expect(show).toHaveBeenCalled();

      await wrapper.setProps({ open: false });
      await flushPromises();
      expect(hide).toHaveBeenCalled();
    } finally {
      delete proto.show;
      delete proto.hide;
    }
  });

  it('teleports the sheet to document.body and cleans it up on unmount', () => {
    const actions = flow();
    const wrapper = mount(PublishSheet, {
      props: { open: false, groups: [AURORA], contentBase: '', ...actions },
    });

    const sheet = document.body.querySelector('nldd-sheet');
    expect(sheet).not.toBeNull();
    expect(sheet?.parentElement).toBe(document.body);

    wrapper.unmount();
    expect(document.body.querySelector('nldd-sheet')).toBeNull();
  });
});

describe('PublishSheet (dragging)', () => {
  /** Enough of DataTransfer for `resolveDroppedFile`: `types`, `files`, and
   * `items` with `webkitGetAsEntry`. */
  function fakeDataTransfer(files: File[], directory = false): DataTransfer {
    return {
      types: files.length ? ['Files'] : [],
      files,
      items: files.map(() => ({
        kind: 'file',
        webkitGetAsEntry: () => (directory ? { isDirectory: true } : { isDirectory: false }),
      })),
    } as unknown as DataTransfer;
  }

  function dragEvent(type: string, dt: DataTransfer): Event {
    const event = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(event, 'dataTransfer', { value: dt });
    return event;
  }

  function sheetEl(wrapper: Wrapper): HTMLElement {
    return wrapper.find('nldd-sheet').element as HTMLElement;
  }

  it('fills the file on a drag of a valid file onto the whole sheet', async () => {
    const { wrapper } = mountComponent();

    sheetEl(wrapper).dispatchEvent(dragEvent('drop', fakeDataTransfer([file('mijn-site.zip')])));
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe('Mijn site');
    expect(wrapper.find('[data-testid="publish-drag-error"]').exists()).toBe(false);
    // No shadow DOM here, so the field cannot be given the file and the chip
    // is the fallback. In a browser the field holds it and shows it itself.
    expect(textOf(wrapper, 'publish-file-chosen')).toContain('mijn-site.zip');
  });

  it('does not mark the field as invalid while a dragged file is held', async () => {
    const { wrapper, actions } = mountComponent();

    sheetEl(wrapper).dispatchEvent(dragEvent('drop', fakeDataTransfer([file('mijn-site.zip')])));
    await flushPromises();

    // Its own required rule would block the submit over a file it never got.
    const field = wrapper.find('[data-testid="publish-file"]');
    expect(field.attributes('required')).toBeUndefined();
    expect(field.attributes('invalid')).toBeUndefined();

    await submit(wrapper);
    expect(actions.publish).toHaveBeenCalled();
  });

  it('gives the field its own requirement back once the dragged file is gone', async () => {
    const { wrapper } = mountComponent();

    sheetEl(wrapper).dispatchEvent(dragEvent('drop', fakeDataTransfer([file('mijn-site.zip')])));
    await flushPromises();
    await wrapper.find('[data-testid="publish-file-chosen"]').trigger('dismiss');
    await flushPromises();

    expect(wrapper.find('[data-testid="publish-file"]').attributes('required')).toBeDefined();
  });

  it('clears a dragged file again, with the same button as on a choice', async () => {
    const { wrapper, actions } = mountComponent();

    sheetEl(wrapper).dispatchEvent(dragEvent('drop', fakeDataTransfer([file('mijn-site.zip')])));
    await flushPromises();

    await wrapper.find('[data-testid="publish-file-chosen"]').trigger('dismiss');
    await flushPromises();

    expect(wrapper.find('[data-testid="publish-file-chosen"]').exists()).toBe(false);
    await submit(wrapper);
    expect(actions.publish).not.toHaveBeenCalled();
  });

  it('refuses two files at once, without filling the file', async () => {
    const { wrapper, actions } = mountComponent();

    sheetEl(wrapper).dispatchEvent(
      dragEvent('drop', fakeDataTransfer([file('een.zip'), file('twee.zip')])),
    );
    await flushPromises();

    expect(wrapper.find('[data-testid="publish-drag-error"]').exists()).toBe(true);
    await submit(wrapper);
    expect(actions.publish).not.toHaveBeenCalled();
  });

  it('refuses a folder', async () => {
    const { wrapper, actions } = mountComponent();

    sheetEl(wrapper).dispatchEvent(
      dragEvent('drop', fakeDataTransfer([file('map')], true)),
    );
    await flushPromises();

    expect(wrapper.find('[data-testid="publish-drag-error"]').exists()).toBe(true);
    await submit(wrapper);
    expect(actions.publish).not.toHaveBeenCalled();
  });

  it('refuses a file type the API does not accept', async () => {
    const { wrapper, actions } = mountComponent();

    sheetEl(wrapper).dispatchEvent(dragEvent('drop', fakeDataTransfer([file('foto.png')])));
    await flushPromises();

    expect(wrapper.find('[data-testid="publish-drag-error"]').exists()).toBe(true);
    await submit(wrapper);
    expect(actions.publish).not.toHaveBeenCalled();
  });

  it('shows the drag state on dragenter and hides it again on dragleave or drop', async () => {
    const { wrapper } = mountComponent();
    const el = sheetEl(wrapper);

    el.dispatchEvent(dragEvent('dragenter', fakeDataTransfer([file('mijn-site.zip')])));
    await flushPromises();
    expect(wrapper.find('[data-testid="publish-drag-active"]').exists()).toBe(true);

    el.dispatchEvent(dragEvent('dragleave', fakeDataTransfer([file('mijn-site.zip')])));
    await flushPromises();
    expect(wrapper.find('[data-testid="publish-drag-active"]').exists()).toBe(false);

    el.dispatchEvent(dragEvent('dragenter', fakeDataTransfer([file('mijn-site.zip')])));
    await flushPromises();
    expect(wrapper.find('[data-testid="publish-drag-active"]').exists()).toBe(true);

    el.dispatchEvent(dragEvent('drop', fakeDataTransfer([file('mijn-site.zip')])));
    await flushPromises();
    expect(wrapper.find('[data-testid="publish-drag-active"]').exists()).toBe(false);
  });

  it('shows the size in kB, then MB, once it steps past the next unit', async () => {
    const { wrapper } = mountComponent();

    sheetEl(wrapper).dispatchEvent(dragEvent('drop', fakeDataTransfer([bigFile('groot.zip', 2_500)])));
    await flushPromises();
    expect(textOf(wrapper, 'publish-file-chosen')).toContain('2,5 kB');

    await wrapper.find('[data-testid="publish-file-chosen"]').trigger('dismiss');
    sheetEl(wrapper).dispatchEvent(
      dragEvent('drop', fakeDataTransfer([bigFile('reusachtig.zip', 2_500_000)])),
    );
    await flushPromises();
    expect(textOf(wrapper, 'publish-file-chosen')).toContain('2,5 MB');
  });

  it('ignores a drop that carries no file at all, such as dragged text', async () => {
    const { wrapper } = mountComponent();

    sheetEl(wrapper).dispatchEvent(dragEvent('drop', fakeDataTransfer([])));
    await flushPromises();

    expect(wrapper.find('[data-testid="publish-drag-error"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="publish-file-chosen"]').exists()).toBe(false);
  });

  it('prevents the default on dragover, so a drop can land on the sheet', async () => {
    const { wrapper } = mountComponent();
    const event = dragEvent('dragover', fakeDataTransfer([file('mijn-site.zip')])) as DragEvent;

    sheetEl(wrapper).dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
  });

  it('dismisses the drop error banner on its own dismiss', async () => {
    const { wrapper } = mountComponent();

    sheetEl(wrapper).dispatchEvent(dragEvent('drop', fakeDataTransfer([file('foto.png')])));
    await flushPromises();
    expect(wrapper.find('[data-testid="publish-drag-error"]').exists()).toBe(true);

    await wrapper.find('[data-testid="publish-drag-error"]').trigger('dismiss');

    expect(wrapper.find('[data-testid="publish-drag-error"]').exists()).toBe(false);
  });

  it('lets a second drop win over the async handoff of the first, without racing it', async () => {
    const { wrapper } = mountComponent();

    sheetEl(wrapper).dispatchEvent(dragEvent('drop', fakeDataTransfer([file('eerste.zip')])));
    sheetEl(wrapper).dispatchEvent(dragEvent('drop', fakeDataTransfer([file('tweede.zip')])));
    for (let i = 0; i < 5; i += 1) await flushPromises();

    expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe('Eerste');
    expect(textOf(wrapper, 'publish-file-chosen')).toContain('tweede.zip');
  });

  it('fills the file right away when the sheet opens with a file already chosen', async () => {
    const actions = flow();
    const wrapper = mount(PublishSheet, {
      props: {
        open: false,
        groups: [AURORA],
        contentBase: 'https://sites.plak.test',
        initialFile: file('gedropt.zip'),
        ...actions,
      },
      global: { stubs: { teleport: true } },
    });

    await wrapper.setProps({ open: true });
    await flushPromises();

    expect(wrapper.find('nldd-text-field[name="title"]').attributes('value')).toBe('Gedropt');
  });

  it('slots an nldd-page, the component that brings the sheet its scroller', () => {
    // nldd-sheet is `overflow: hidden` with `--context-scroll-mode: nested`,
    // so a slotted container is clipped rather than scrolled: on a short
    // viewport the submit button then cannot be reached at all.
    const { wrapper } = mountComponent();

    const page = wrapper.find('nldd-sheet > nldd-page');
    expect(page.exists()).toBe(true);
    expect(page.element.querySelector(':scope > nldd-container')).not.toBeNull();
  });

  it('puts each exception hint beside its switch, where it renders', () => {
    // nldd-switch-field has no slot of its own: a help text written inside it
    // stays in the DOM and never reaches the screen, which jsdom cannot see.
    const { wrapper } = mountComponent();

    for (const id of ['publish-exception-keys', 'publish-exception-invitees']) {
      const control = wrapper.find(`[data-testid="${id}"]`).element;
      expect(control.querySelector('nldd-form-field-help-text')).toBeNull();
      const field = control.closest('nldd-form-field');
      expect(field, `${id} has no nldd-form-field around it`).not.toBeNull();
      expect(field!.querySelector(':scope > nldd-form-field-help-text')?.textContent).toBeTruthy();
    }
  });
});
