import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
import ConfirmModal from '@/components/ConfirmModal.vue';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { _setLocaleForTest } from '@/i18n';
import TabDeploy from './TabDeploy.vue';
import { serverErrorFetch, untilIdle, fireDetailEvent } from './testHelpers';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  // /me is session-cached across tests; the "zichtbaarheid naar rol" cases
  // switch the logged-in member, so the cache must not leak between them.
  _resetCurrentMemberCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeWrapper() {
  return mount(TabDeploy, {
    props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
    global: { stubs: { teleport: true } },
  });
}

/**
 * Wraps the mock backend's fetch, patching fields onto the `/me` response
 * only, for scenarios the mock's fixed data does not otherwise reach (no
 * session, no configured Forgejo host, no CI audience).
 */
function withMeOverride(overrides: Record<string, unknown>): typeof fetch {
  return async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    const response = await backend.fetch(input, init);
    if (!url.endsWith('/me') || !response.ok) return response;
    const body = (await response.json()) as Record<string, unknown>;
    return new Response(JSON.stringify({ ...body, ...overrides }), {
      status: response.status,
      headers: response.headers,
    });
  };
}

describe('TabDeploy: linked repository', () => {
  it('shows the linked repository with host, live branch and who linked it', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-name"]').text()).toContain(
      'GitHub - team-aurora/website',
    );
    expect(wrapper.find('[data-testid="repository-live-branch"]').text()).toContain('main');
    expect(wrapper.html()).toContain('Bea Heerder');
    expect(wrapper.find('[data-testid="repository-unconfirmed"]').exists()).toBe(false);
  });

  it('says the name and ids are as entered while nothing has confirmed them', async () => {
    backend.data.repositories[0]!.idsConfirmed = false;

    const wrapper = makeWrapper();
    await untilIdle();

    const note = wrapper.find('[data-testid="repository-unconfirmed"]');
    expect(note.attributes('text')).toBe('Nog niet bevestigd');
    expect(note.attributes('supporting-text')).toContain('zoals ze zijn ingevuld');
  });

  it('shows "elke branch" when there is no live branch restriction', async () => {
    backend.data.repositories[0]!.liveBranch = null;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-live-branch"]').text()).toContain('elke branch');
  });

  it('shows the empty state and the link form without a linked repository', async () => {
    backend.data.repositories = [];

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-empty"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="workflow-snippet"]').exists()).toBe(false);

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(true);
  });

  it('shows an error message on a server error while loading', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.html()).toContain('Serverfout');
  });
});

describe('TabDeploy: linking and changing the repository', () => {
  it('links a new GitHub repository with validation on owner/repo', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    // No slash: refused client-side, nothing submitted.
    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'ongeldig',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.find('[data-testid="repository-owner-repo"]').attributes('invalid')).toBeDefined();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'minbzk/website',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(1);
    expect(backend.data.repositories[0]).toMatchObject({
      groupSlug: 'team-aurora',
      siteSlug: 'website',
      provider: 'github',
      owner: 'minbzk',
      repo: 'website',
      // Live-branch is not prefilled; an untouched field means every branch
      // may publish live.
      liveBranch: null,
    });
    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(false);
    expect(wrapper.find('nldd-notification[variant="success"]').exists()).toBe(true);
  });

  it('fills in a host from ciForgejoHosts for Forgejo and sends it along', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    const providerSelect = wrapper.find('[data-testid="repository-provider"]');
    await providerSelect.setValue('forgejo');
    await untilIdle();

    const hostSelect = wrapper.find('[data-testid="repository-host"]');
    expect(hostSelect.exists()).toBe(true);
    expect(hostSelect.findAll('option').map((o) => o.element.value)).toEqual([
      'https://code.overheid.nl',
    ]);

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'robbertbos/waggle',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]).toMatchObject({
      provider: 'forgejo',
      host: 'https://code.overheid.nl',
      owner: 'robbertbos',
      repo: 'waggle',
    });
  });

  it('leaves an empty live branch empty (every branch may publish live)', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'minbzk/website',
    });
    fireDetailEvent(wrapper.find('[data-testid="repository-live-branch-input"]').element, 'input', {
      value: '',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]!.liveBranch).toBeNull();
  });

  it('starts the link form with an empty live branch field, not prefilled with main', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    expect(
      (wrapper.find('[data-testid="repository-live-branch-input"]').element as HTMLInputElement).getAttribute(
        'value',
      ),
    ).toBe('');
  });

  it('sends an explicitly filled in live branch along', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'minbzk/website',
    });
    fireDetailEvent(wrapper.find('[data-testid="repository-live-branch-input"]').element, 'input', {
      value: 'main',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]!.liveBranch).toBe('main');
  });

  it('recognizes a pasted GitHub URL and derives provider and owner/repo', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'https://github.com/minbzk/website.git',
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-recognized"]').text()).toContain(
      'Herkend: GitHub, minbzk/website',
    );
    expect(
      (wrapper.find('[data-testid="repository-provider"]').element as HTMLSelectElement).value,
    ).toBe('github');

    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]).toMatchObject({
      provider: 'github',
      owner: 'minbzk',
      repo: 'website',
    });
  });

  it('recognizes a pasted Forgejo URL with /tree/<branch> and sets the host', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'https://code.overheid.nl/robbertbos/waggle/tree/main',
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-recognized"]').text()).toContain(
      'Herkend: Forgejo (code.overheid.nl), robbertbos/waggle',
    );
    expect(
      (wrapper.find('[data-testid="repository-provider"]').element as HTMLSelectElement).value,
    ).toBe('forgejo');
    expect(
      (wrapper.find('[data-testid="repository-host"]').element as HTMLSelectElement).value,
    ).toBe('https://code.overheid.nl');

    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]).toMatchObject({
      provider: 'forgejo',
      host: 'https://code.overheid.nl',
      owner: 'robbertbos',
      repo: 'waggle',
    });
  });

  it('recognizes a git@ address', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'git@github.com:minbzk/website.git',
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-recognized"]').text()).toContain(
      'Herkend: GitHub, minbzk/website',
    );
  });

  it('refuses a URL with an unknown host, with a clear message', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'https://gitlab.com/minbzk/website',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.find('[data-testid="repository-owner-repo"]').attributes('invalid')).toBeDefined();
    expect(wrapper.html()).toContain('Onbekende host "gitlab.com"');
    expect(wrapper.html()).toContain('github.com');
    expect(wrapper.html()).toContain('code.overheid.nl');
  });

  it('still just accepts "owner/repo" without a URL', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'minbzk/website',
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-recognized"]').exists()).toBe(false);

    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]).toMatchObject({ provider: 'github', owner: 'minbzk', repo: 'website' });
  });

  it('opens the form prefilled on Change and updates the link', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();

    expect(
      (wrapper.find('[data-testid="repository-owner-repo"]').element as HTMLInputElement).getAttribute(
        'value',
      ),
    ).toBe('team-aurora/website');

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'team-aurora/nieuwe-website',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]!.repo).toBe('nieuwe-website');
    expect(wrapper.find('[data-testid="repository-name"]').text()).toContain('nieuwe-website');
  });

  it('closes the form on cancel without saving anything', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();
    await wrapper.find('[data-testid="repository-cancel"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(false);
    expect(backend.data.repositories[0]!.repo).toBe('website');
  });

  it('refuses an empty owner/repo client-side, without a request to the server', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: '',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    // Empty after the slash: refused client-side before the request goes out.
    expect(wrapper.find('[data-testid="repository-owner-repo"]').attributes('invalid')).toBeDefined();
  });

  it('shows the server error message for a failed link', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'minbzk/website',
    });
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if ((init?.method ?? 'GET') === 'PUT' && url.includes('/repository')) {
        return Promise.resolve(
          new Response(
            JSON.stringify({
              type: 'about:blank',
              title: 'Repository niet gevonden',
              status: 422,
              detail: 'Dit repository bestaat niet of is niet publiek.',
              code: 'REPOSITORY_NOT_FOUND',
            }),
            { status: 422, headers: { 'content-type': 'application/problem+json' } },
          ),
        );
      }
      return backend.fetch(input, init);
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(true);
    expect(wrapper.html()).toContain('Dit repository bestaat niet of is niet publiek.');
  });
});

/**
 * Records every PUT body to `/repository`; `answer` may replace the mock
 * backend's response for a given body.
 */
function recordRepositoryPuts(answer?: (body: Record<string, unknown>) => Response | undefined) {
  const bodies: Record<string, unknown>[] = [];
  vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    if ((init?.method ?? 'GET') === 'PUT' && url.includes('/repository')) {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>;
      bodies.push(body);
      const replaced = answer?.(body);
      if (replaced) return Promise.resolve(replaced);
    }
    return backend.fetch(input, init);
  });
  return bodies;
}

function problemResponse(status: number, code: string, detail: string): Response {
  return new Response(JSON.stringify({ type: 'about:blank', title: code, status, detail, code }), {
    status,
    headers: { 'content-type': 'application/problem+json' },
  });
}

const notFoundWithoutIds = (body: Record<string, unknown>) =>
  body.repositoryId === undefined ? problemResponse(422, 'REPOSITORY_NOT_FOUND', 'Niet gevonden.') : undefined;

async function submitForm(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
  await wrapper.find('[data-testid="repository-form"]').trigger('submit');
  await untilIdle();
}

function typeInto(wrapper: ReturnType<typeof makeWrapper>, testid: string, value: string): void {
  fireDetailEvent(wrapper.find(`[data-testid="${testid}"]`).element, 'input', { value });
}

async function openLinkForm(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
  await untilIdle();
  await wrapper.find('[data-testid="repository-link"]').trigger('click');
  await untilIdle();
}

describe('TabDeploy: a repository Plak cannot look up', () => {
  it('offers the id fields only after the lookup failed, then links with the entered ids', async () => {
    backend.data.repositories = [];
    const bodies = recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    expect(wrapper.find('[data-testid="repository-id"]').exists()).toBe(false);

    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-site');
    await submitForm(wrapper);

    expect(bodies[0]).not.toHaveProperty('repositoryId');
    expect(wrapper.html()).toContain('vul dan het repository-id en het eigenaar-id zelf in');
    expect(wrapper.find('[data-testid="repository-ids-command"]').text()).toBe(
      "gh api repos/team-aurora/prive-site --jq '.id, .owner.id'",
    );
    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe('');

    typeInto(wrapper, 'repository-id', ' 5005 ');
    typeInto(wrapper, 'repository-owner-id', '6006');
    await submitForm(wrapper);

    expect(bodies[1]).toMatchObject({ owner: 'team-aurora', repo: 'prive-site', repositoryId: 5005, ownerId: 6006 });
    expect(backend.data.repositories[0]).toMatchObject({ repo: 'prive-site', repositoryId: 5005, ownerId: 6006 });
    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(false);
  });

  it('sends no ids while both fields stay empty, and keeps the fields in view', async () => {
    backend.data.repositories = [];
    const bodies = recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-site');
    await submitForm(wrapper);
    await submitForm(wrapper);

    expect(bodies).toHaveLength(2);
    expect(bodies[1]).not.toHaveProperty('repositoryId');
    expect(bodies[1]).not.toHaveProperty('ownerId');
    expect(wrapper.find('[data-testid="repository-id"]').exists()).toBe(true);
  });

  it.each([
    ['5005', ''],
    ['', '6006'],
    ['5005', 'abc'],
    ['-1', '6006'],
    ['50.5', '6006'],
  ])('refuses ids %j and %j client-side, without a request', async (repositoryId, ownerId) => {
    backend.data.repositories = [];
    const bodies = recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-site');
    await submitForm(wrapper);

    typeInto(wrapper, 'repository-id', repositoryId);
    typeInto(wrapper, 'repository-owner-id', ownerId);
    await submitForm(wrapper);

    expect(bodies).toHaveLength(1);
    expect(wrapper.find('[data-testid="repository-id"]').attributes('invalid')).toBeDefined();
    expect(wrapper.find('[data-testid="repository-owner-id"]').attributes('unmet')).toBe('repository-ids-error');
    expect(wrapper.find('#repository-ids-error').text()).toBe(
      'Vul het repository-id en het eigenaar-id allebei in, alleen met cijfers.',
    );
  });

  it('shows an ids refusal from the server at the id fields, not at the repository', async () => {
    backend.data.repositories = [];
    recordRepositoryPuts((body) =>
      body.repositoryId === undefined
        ? problemResponse(422, 'REPOSITORY_NOT_FOUND', 'Niet gevonden.')
        : problemResponse(422, 'REPOSITORY_IDS_MISMATCH', 'GitHub geeft andere ids.'),
    );
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/website');
    await submitForm(wrapper);
    typeInto(wrapper, 'repository-id', '1');
    typeInto(wrapper, 'repository-owner-id', '2');
    await submitForm(wrapper);

    expect(wrapper.find('#repository-ids-error').text()).toBe('GitHub geeft andere ids.');
    expect(wrapper.find('#repository-server').text()).toBe('');
    expect(wrapper.find('[data-testid="repository-owner-repo"]').attributes('invalid')).toBeUndefined();
  });

  it('keeps the command on the repository it failed for while the input is mid-edit', async () => {
    backend.data.repositories = [];
    recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-site');
    await submitForm(wrapper);

    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-ids-command"]').text()).toContain('repos/team-aurora/prive-site');

    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-docs');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-ids-command"]').text()).toContain('repos/team-aurora/prive-docs');
  });

  it('names the Forgejo API for a Forgejo repository', async () => {
    backend.data.repositories = [];
    recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    await wrapper.find('[data-testid="repository-provider"]').setValue('forgejo');
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-site');
    await submitForm(wrapper);

    expect(wrapper.find('[data-testid="repository-ids-command"]').text()).toBe(
      `curl -s -H "Authorization: token <token>" https://code.overheid.nl/api/v1/repos/team-aurora/prive-site | jq '.id, .owner.id'`,
    );
  });

  it('prefills the stored ids when a linked repository turns out private on Change', async () => {
    const linked = backend.data.repositories[0]!;
    const bodies = recordRepositoryPuts(notFoundWithoutIds);
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();
    typeInto(wrapper, 'repository-owner-repo', `${linked.owner}/${linked.repo}`.toUpperCase());
    typeInto(wrapper, 'repository-live-branch-input', 'release');
    await submitForm(wrapper);

    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe(String(linked.repositoryId));
    expect(wrapper.find('[data-testid="repository-owner-id"]').attributes('value')).toBe(String(linked.ownerId));

    // Another spelling of the same repository keeps them.
    typeInto(wrapper, 'repository-owner-repo', `https://github.com/${linked.owner}/${linked.repo}`);
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe(String(linked.repositoryId));

    await submitForm(wrapper);
    expect(bodies[1]).toMatchObject({
      repositoryId: linked.repositoryId,
      ownerId: linked.ownerId,
      liveBranch: 'release',
    });
    expect(wrapper.find('[data-testid="repository-live-branch"]').text()).toContain('release');
  });

  it('prefills the stored ids of a linked Forgejo repository on the same host', async () => {
    backend.data.repositories = [
      { ...backend.data.repositories[0]!, provider: 'forgejo', host: 'https://code.overheid.nl' },
    ];
    recordRepositoryPuts(notFoundWithoutIds);
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();
    await submitForm(wrapper);

    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe(
      String(backend.data.repositories[0]!.repositoryId),
    );
  });

  it('drops prefilled ids once the form names another repository, even if it names the linked one again', async () => {
    const linked = backend.data.repositories[0]!;
    const bodies = recordRepositoryPuts(notFoundWithoutIds);
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();
    await submitForm(wrapper);
    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe(String(linked.repositoryId));

    typeInto(wrapper, 'repository-owner-repo', `${linked.owner}/${linked.repo}-oud`);
    await untilIdle();
    typeInto(wrapper, 'repository-owner-repo', `${linked.owner}/${linked.repo}`);
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe('');
    expect(wrapper.find('[data-testid="repository-owner-id"]').attributes('value')).toBe('');
    await submitForm(wrapper);
    expect(bodies[1]).not.toHaveProperty('repositoryId');
  });

  it('drops prefilled ids when the provider changes, but keeps ids typed by hand', async () => {
    recordRepositoryPuts(notFoundWithoutIds);
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();
    await submitForm(wrapper);

    await wrapper.find('[data-testid="repository-provider"]').setValue('forgejo');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe('');

    typeInto(wrapper, 'repository-id', '5005');
    typeInto(wrapper, 'repository-owner-id', '6006');
    await wrapper.find('[data-testid="repository-provider"]').setValue('github');
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-docs');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe('5005');
  });

  it('does not prefill the stored ids for another repository', async () => {
    recordRepositoryPuts(notFoundWithoutIds);
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-docs');
    await submitForm(wrapper);

    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe('');
    expect(wrapper.find('[data-testid="repository-owner-id"]').attributes('value')).toBe('');
  });

  it('shows the generic message and no id fields when the request itself fails', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-site');
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline')));
    await submitForm(wrapper);

    expect(wrapper.find('#repository-server').text()).toBe('Koppelen is niet gelukt.');
    expect(wrapper.find('[data-testid="repository-id"]').exists()).toBe(false);
  });

  it('points at plak site link, with the live branch the form holds', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    const hint = () => wrapper.find('[data-testid="repository-cli-hint"]');
    const command = () => hint().find('nldd-code-viewer[data-testid="repository-cli-command"]');

    expect(command().text()).toBe('plak site link team-aurora/website');
    expect(command().attributes('no-copy')).toBeUndefined();
    expect(hint().text()).toContain('haalt de CLI de ids zelf op via gh');

    typeInto(wrapper, 'repository-live-branch-input', ' main ');
    await untilIdle();
    expect(command().text()).toBe('plak site link team-aurora/website --live-branch main');

    // Named, the repository no longer depends on the checkout the command runs in.
    typeInto(wrapper, 'repository-owner-repo', 'https://github.com/minbzk/website.git');
    await untilIdle();
    expect(command().text()).toBe('plak site link team-aurora/website minbzk/website --live-branch main');

    typeInto(wrapper, 'repository-owner-repo', 'minbzk/');
    await untilIdle();
    expect(command().text()).toBe('plak site link team-aurora/website --live-branch main');
  });

  it('names a Forgejo repository by its URL, since a bare owner/repo means GitHub', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    await wrapper.find('[data-testid="repository-provider"]').setValue('forgejo');
    typeInto(wrapper, 'repository-owner-repo', 'minbzk/website');
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-cli-command"]').text()).toBe(
      'plak site link team-aurora/website https://code.overheid.nl/minbzk/website',
    );
  });

  it('names the linked repository when the form is opened to change it', async () => {
    const linked = backend.data.repositories[0]!;
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-cli-command"]').text()).toContain(
      `plak site link team-aurora/website ${linked.owner}/${linked.repo}`,
    );
  });

  it('names plak site link first for a private GitHub repository, and only once', async () => {
    backend.data.repositories = [];
    recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-site');
    typeInto(wrapper, 'repository-live-branch-input', 'main');
    await submitForm(wrapper);

    const explanation = wrapper.find('[data-testid="repository-ids-explanation"]');
    const blocks = explanation.findAll('nldd-code-viewer').map((block) => block.attributes('data-testid'));
    expect(blocks).toEqual(['repository-ids-cli', 'repository-ids-command']);
    expect(explanation.find('[data-testid="repository-ids-cli"]').text()).toBe(
      'plak site link team-aurora/website team-aurora/prive-site --live-branch main',
    );
    expect(explanation.text()).toContain('Een verkeerd id koppelt niets anders');
    expect(wrapper.find('[data-testid="repository-cli-hint"]').exists()).toBe(false);
  });

  it('keeps the general hint for Forgejo, where the CLI does not fetch the ids', async () => {
    backend.data.repositories = [];
    recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    await wrapper.find('[data-testid="repository-provider"]').setValue('forgejo');
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-site');
    await submitForm(wrapper);

    expect(wrapper.find('[data-testid="repository-ids-cli"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-ids-explanation"]').text()).toContain('Vul dan de twee ids hieronder zelf in');
    expect(wrapper.find('[data-testid="repository-cli-hint"]').exists()).toBe(true);
  });

  it('points at plak site link in English too', async () => {
    _setLocaleForTest('en');
    try {
      backend.data.repositories = [];
      const wrapper = makeWrapper();
      await openLinkForm(wrapper);
      const hint = wrapper.find('[data-testid="repository-cli-hint"]');
      expect(hint.text()).toContain('Rather from the terminal? Run this in a checkout of the repository');
      expect(hint.find('[data-testid="repository-cli-command"]').text()).toBe(
        'plak site link team-aurora/website',
      );
    } finally {
      _setLocaleForTest('nl');
    }
  });

  it('hides the id fields again when the form is reopened', async () => {
    backend.data.repositories = [];
    recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-owner-repo', 'team-aurora/prive-site');
    await submitForm(wrapper);
    expect(wrapper.find('[data-testid="repository-id"]').exists()).toBe(true);

    await wrapper.find('[data-testid="repository-cancel"]').trigger('click');
    await untilIdle();
    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-id"]').exists()).toBe(false);
  });
});

describe('TabDeploy: unlinking the repository', () => {
  it('asks for confirmation and only then unlinks', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-unlink"]').trigger('click');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(1);

    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.find('[data-testid="repository-empty"]').exists()).toBe(true);
  });

  it('reports it when unlinking fails', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-unlink"]').trigger('click');
    vi.stubGlobal('fetch', serverErrorFetch());
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('nldd-notification[variant="critical"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="repository-name"]').exists()).toBe(true);
  });
});

describe('TabDeploy: visibility by role', () => {
  it('editor sees no link form or buttons, but does see the linked repository and the snippet', async () => {
    // lid-3 (Ada Vermeer) is editor in group team-aurora, not a site admin.
    backend.data.loggedInMemberId = 'lid-3';

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-change"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-unlink"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-link"]').exists()).toBe(false);

    expect(wrapper.find('[data-testid="repository-name"]').text()).toContain(
      'GitHub - team-aurora/website',
    );
    expect(wrapper.find('[data-testid="workflow-snippet"]').exists()).toBe(true);
  });

  it('editor without a linked repository sees a reference to the site admin', async () => {
    backend.data.loggedInMemberId = 'lid-3';
    backend.data.repositories = [];

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-link"]').exists()).toBe(false);
    const empty = wrapper.find('[data-testid="repository-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.attributes('supporting-text')).toContain('Vraag een beheerder van deze site');
  });

  it('site admin (platform admin) sees the link form and the buttons', async () => {
    // The default logged-in user (lid-1) is a platform admin.
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-change"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="repository-unlink"]').exists()).toBe(true);

    backend.data.repositories = [];
    const emptyWrapper = makeWrapper();
    await untilIdle();

    expect(emptyWrapper.find('[data-testid="repository-link"]').exists()).toBe(true);
    await emptyWrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();
    expect(emptyWrapper.find('[data-testid="repository-form"]').exists()).toBe(true);
  });
});

describe('TabDeploy: workflow snippet and curl fallback', () => {
  it('fills the github snippet with host, site and no secrets', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const snippet = wrapper.find('[data-testid="workflow-snippet"]').text();
    expect(snippet).toContain('host: https://plak.test');
    expect(snippet).toContain('site: team-aurora/website');
    expect(snippet).toContain('DigiGilde/plak/actions/publish@<commit-sha>');
    expect(snippet).toContain('id-token: write');
    expect(snippet).toContain('branches: [main]');
    expect(snippet).toContain('preview-ref: pr-${{ github.event.pull_request.number }}');
    expect(snippet).toContain('teardown: "true"');
    expect(snippet).not.toContain('secrets.');
    expect(snippet).not.toContain(MOCK_CONTENT_BASE);
  });

  it('fills the forgejo snippet with enable-openid-connect and the full action URL', async () => {
    backend.data.repositories[0]!.provider = 'forgejo';
    backend.data.repositories[0]!.host = 'https://code.overheid.nl';

    const wrapper = makeWrapper();
    await untilIdle();

    const snippet = wrapper.find('[data-testid="workflow-snippet"]').text();
    expect(snippet).toContain('enable-openid-connect: true');
    expect(snippet).toContain('uses: https://github.com/DigiGilde/plak/actions/publish@<commit-sha>');
    expect(snippet).toContain('runs-on: docker');
    expect(snippet).not.toContain('secrets.');
  });

  it('shows the cli commands for login, live, preview and logout with host and site filled in', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const snippet = wrapper.find('[data-testid="cli-snippet"]').text();
    expect(snippet).toContain('plak login --host https://plak.test');
    expect(snippet).toContain('plak publish ./dist --host https://plak.test --site team-aurora/website');
    expect(snippet).toContain(
      `plak publish ./dist --host https://plak.test --site team-aurora/website --site-id ${backend.data.sites[0]!.id} --preview pr-42`,
    );
    expect(snippet).toContain('plak logout');
    expect(snippet).not.toContain(MOCK_CONTENT_BASE);
  });

  it('links to the plak repo for the cli and to /cli-link', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const cliLink = wrapper.find('[data-testid="plak-cli-repo-link"]');
    expect(cliLink.attributes('href')).toBe('https://github.com/DigiGilde/plak');

    const linkCliLink = wrapper.find('[data-testid="cli-link-link"]');
    expect(linkCliLink.attributes('href')).toBe('/cli-link');
    expect(wrapper.html()).toContain('plak login');
  });

  it('gives the install and upgrade commands as one copyable block', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const install = wrapper.find('nldd-code-viewer[data-testid="cli-install"]');
    expect(install.text().split('\n')).toEqual([
      'uv tool install "git+https://github.com/DigiGilde/plak@beta#subdirectory=cli"',
      'uv tool upgrade plak',
    ]);
    expect(install.attributes('no-copy')).toBeUndefined();
    expect(install.element.parentElement!.textContent).toContain('uv run --project cli plak ...');
  });

  it('names the workflow file path and links to the versions tab', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const richText = wrapper.find('[data-testid="workflow-snippet"]').element.parentElement!;
    expect(richText.textContent).toContain('.github/workflows/publish.yml');

    const versionsLink = wrapper.find('[data-testid="deploy-to-versions"]');
    expect(versionsLink.attributes('href')).toBe('/team-aurora/website/versions');
    expect(versionsLink.text()).toBe('Versies');
  });

  it('names the Forgejo workflow file path for a Forgejo repository', async () => {
    backend.data.repositories[0]!.provider = 'forgejo';
    backend.data.repositories[0]!.host = 'https://code.overheid.nl';

    const wrapper = makeWrapper();
    await untilIdle();

    const richText = wrapper.find('[data-testid="workflow-snippet"]').element.parentElement!;
    expect(richText.textContent).toContain('.forgejo/workflows/publish.yml');
  });
});

/** The tab holds two confirmation dialogs: the one for unlinking, then the one for requiring the site id. */
function requireDialog(wrapper: ReturnType<typeof makeWrapper>) {
  return wrapper.findAllComponents(ConfirmModal)[1]!;
}

/** Stands in for the clipboard; `refuse` makes writing to it fail, as it does without permission. */
function stubClipboard(refuse = false) {
  const writeText = refuse
    ? vi.fn().mockRejectedValue(new Error('geen toestemming'))
    : vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  return writeText;
}

const REQUIRED_SENTENCE = 'Alleen workflows die het site-ID noemen, kunnen publiceren.';
const OPTIONAL_FOR_ADMIN =
  "Deze koppeling accepteert ook workflows die het site-ID niet noemen. Zet site-id in je workflow en kies daarna 'Alleen met site-ID publiceren'.";
const OPTIONAL_FOR_READER =
  'Deze koppeling accepteert ook workflows die het site-ID niet noemen. Alleen een beheerder van deze site kan dat beperken.';

describe('TabDeploy: the site id', () => {
  it('shows the id of the site as code that a browser does not translate', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const id = wrapper.find('[data-testid="site-id"]');
    expect(id.element.tagName.toLowerCase()).toBe('code');
    expect(id.text()).toBe(backend.data.sites[0]!.id);
    expect(id.attributes('translate')).toBe('no');
    expect(wrapper.find('[data-testid="site-id-block"]').text()).toContain('Site-ID');
  });

  it('shows no site id while nothing is linked', async () => {
    backend.data.repositories = [];

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-block"]').exists()).toBe(false);
  });

  it('copies the id and says so beside the button', async () => {
    const writeText = stubClipboard();
    const wrapper = makeWrapper();
    await untilIdle();

    const button = wrapper.find('[data-testid="site-id-copy"]');
    expect(button.attributes('text')).toBe('Kopieer site-ID');
    await button.trigger('click');
    await untilIdle();

    expect(writeText).toHaveBeenCalledWith(backend.data.sites[0]!.id);
    const notice = wrapper.find('[data-testid="site-id-copy-notice"]');
    expect(notice.text()).toBe('Site-ID gekopieerd.');
    // A status line that is in the page before it has anything to say, so the copy is announced.
    expect(notice.attributes('role')).toBe('status');
  });

  it('points at the id itself when the clipboard refuses it', async () => {
    stubClipboard(true);
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="site-id-copy"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-copy-notice"]').text()).toBe(
      'Kopiëren lukte niet. Selecteer het site-ID hierboven en kopieer het zelf.',
    );
  });

  it('forgets that the id was copied when the tab moves to another site', async () => {
    stubClipboard();
    backend.data.sites.push({ ...backend.data.sites[0]!, id: 'tweede-id', slug: 'docs', title: 'Docs' });
    backend.data.repositories.push({ ...backend.data.repositories[0]!, siteSlug: 'docs', siteId: 'tweede-id' });
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="site-id-copy"]').trigger('click');
    await untilIdle();
    expect(wrapper.find('[data-testid="site-id-copy-notice"]').text()).toBe('Site-ID gekopieerd.');

    await wrapper.setProps({ site: 'docs' });
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id"]').text()).toBe('tweede-id');
    expect(wrapper.find('[data-testid="site-id-copy-notice"]').text()).toBe('');
  });
});

describe('TabDeploy: the site id in the snippets', () => {
  const stepsOf = (snippet: string, id: string) =>
    snippet.match(new RegExp(`site: team-aurora/website\\n\\s+site-id: ${id}\\n`, 'g'));

  it('names the site id under site: in every step of the GitHub workflow', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const snippet = wrapper.find('[data-testid="workflow-snippet"]').text();
    expect(snippet.match(/site:/g)).toHaveLength(3);
    expect(stepsOf(snippet, backend.data.sites[0]!.id)).toHaveLength(3);
  });

  it('names the site id under site: in every step of the Forgejo workflow', async () => {
    backend.data.repositories[0]!.provider = 'forgejo';
    backend.data.repositories[0]!.host = 'https://code.overheid.nl';

    const wrapper = makeWrapper();
    await untilIdle();

    const snippet = wrapper.find('[data-testid="workflow-snippet"]').text();
    expect(snippet.match(/site:/g)).toHaveLength(3);
    expect(stepsOf(snippet, backend.data.sites[0]!.id)).toHaveLength(3);
  });

  it('adds --site-id to the commands that publish, once a repository is linked', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const id = backend.data.sites[0]!.id;
    const commands = wrapper.find('[data-testid="cli-snippet"]').text().split('\n');
    expect(commands).toContain(
      `plak publish ./dist --host https://plak.test --site team-aurora/website --site-id ${id}`,
    );
    expect(commands).toContain(
      `plak publish ./dist --host https://plak.test --site team-aurora/website --site-id ${id} --preview pr-42`,
    );
  });

  it('leaves --site-id out while nothing is linked, since the tab then knows no id', async () => {
    backend.data.repositories = [];

    const wrapper = makeWrapper();
    await untilIdle();

    const commands = wrapper.find('[data-testid="cli-snippet"]').text().split('\n');
    expect(commands).toContain('plak publish ./dist --host https://plak.test --site team-aurora/website');
    expect(commands).toContain(
      'plak publish ./dist --host https://plak.test --site team-aurora/website --preview pr-42',
    );
    expect(commands.join('\n')).not.toContain('--site-id');
  });

  it('keeps plak site link as it was', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);

    expect(wrapper.find('[data-testid="repository-cli-command"]').text()).toBe(
      'plak site link team-aurora/website',
    );
  });
});

describe('TabDeploy: requiring the site id', () => {
  it('tells an admin the link also accepts a workflow without the id, and offers to end that', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(OPTIONAL_FOR_ADMIN);
    expect(wrapper.find('[data-testid="site-id-requirement"] code').text()).toBe('site-id');
    const button = wrapper.find('[data-testid="site-id-require"]');
    expect(button.attributes('text')).toBe('Alleen met site-ID publiceren');
    expect(button.attributes('variant')).toBe('secondary');
    expect(button.attributes('disabled')).toBeUndefined();
  });

  it('puts the sentence in a status line, so it is announced when it changes', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-requirement"]').attributes('role')).toBe('status');
  });

  it('asks before it requires the site id, and requires it after the confirmation', async () => {
    const bodies = recordRepositoryPuts();
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="site-id-require"]').trigger('click');
    await untilIdle();

    const dialog = requireDialog(wrapper);
    expect(dialog.props('open')).toBe(true);
    expect(dialog.props('title')).toBe('Alleen nog publiceren met het site-ID?');
    expect(dialog.props('text')).toBe(
      'Workflows zonder "site-id" kunnen daarna niet meer publiceren naar deze site. Dit kun je niet terugdraaien.',
    );
    expect(dialog.props('keepLabel')).toBe('Behoud de huidige koppeling');
    expect(dialog.props('confirmLabel')).toBe('Alleen met site-ID');
    expect(bodies).toEqual([]);
    expect(backend.data.repositories[0]!.siteIdRequired).toBe(false);

    await dialog.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(bodies).toEqual([{ siteIdRequired: true }]);
    expect(backend.data.repositories[0]!.siteIdRequired).toBe(true);
    expect(requireDialog(wrapper).props('open')).toBe(false);
    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(REQUIRED_SENTENCE);
    expect(wrapper.find('[data-testid="site-id-require"]').exists()).toBe(false);
  });

  it('leaves the link as it is when the confirmation is cancelled', async () => {
    const bodies = recordRepositoryPuts();
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="site-id-require"]').trigger('click');
    await requireDialog(wrapper).find('[data-testid="confirm-cancel"]').trigger('click');
    await untilIdle();

    expect(requireDialog(wrapper).props('open')).toBe(false);
    expect(bodies).toEqual([]);
    expect(backend.data.repositories[0]!.siteIdRequired).toBe(false);
    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(OPTIONAL_FOR_ADMIN);
    expect(wrapper.find('[data-testid="site-id-require"]').exists()).toBe(true);
  });

  it('shows the dialog as busy while the request is under way', async () => {
    let answer: (response: Response) => void = () => {};
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? 'GET') === 'PUT' && String(input).endsWith('/site-id-required')) {
        return new Promise<Response>((resolve) => {
          answer = resolve;
        });
      }
      return backend.fetch(input, init);
    });
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="site-id-require"]').trigger('click');

    await requireDialog(wrapper).find('[data-testid="confirm-continue"]').trigger('click');
    expect(requireDialog(wrapper).props('busy')).toBe(true);

    answer(
      await backend.fetch('/-/api/v1/sites/team-aurora/website/repository/site-id-required', {
        method: 'PUT',
        body: JSON.stringify({ siteIdRequired: true }),
      }),
    );
    await untilIdle();
    expect(requireDialog(wrapper).props('busy')).toBe(false);
  });

  it('shows only the sentence once the site id is required, to an admin as to anyone', async () => {
    backend.data.repositories[0]!.siteIdRequired = true;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(REQUIRED_SENTENCE);
    expect(wrapper.find('[data-testid="site-id-require"]').exists()).toBe(false);
  });

  it('tells a member who cannot change it that only an admin can, and shows no button', async () => {
    // lid-3 (Ada Vermeer) is editor in the group, not a site admin.
    backend.data.loggedInMemberId = 'lid-3';

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(OPTIONAL_FOR_READER);
    expect(wrapper.find('[data-testid="site-id-require"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="site-id"]').text()).toBe(backend.data.sites[0]!.id);
  });

  it('shows a member who cannot change it the same sentence once the site id is required', async () => {
    backend.data.loggedInMemberId = 'lid-3';
    backend.data.repositories[0]!.siteIdRequired = true;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(REQUIRED_SENTENCE);
    expect(wrapper.find('[data-testid="site-id-require"]').exists()).toBe(false);
  });

  it('gives a platform admin without a role on the site no button, since the API gives that role no say', async () => {
    // lid-1 is a platform admin and, in the mock, also admin of the group: take that away.
    backend.data.groupMembers = backend.data.groupMembers.filter((member) => member.memberId !== 'lid-1');

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(OPTIONAL_FOR_READER);
    expect(wrapper.find('[data-testid="site-id-require"]').exists()).toBe(false);
  });

  it('shows no button to someone who is not logged in', async () => {
    backend.data.loggedInMemberId = null;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(OPTIONAL_FOR_READER);
    expect(wrapper.find('[data-testid="site-id-require"]').exists()).toBe(false);
  });

  it('requires the site id from the start on a new link', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);

    typeInto(wrapper, 'repository-owner-repo', 'minbzk/website');
    await submitForm(wrapper);

    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(REQUIRED_SENTENCE);
    expect(wrapper.find('[data-testid="site-id-require"]').exists()).toBe(false);
  });

  it('keeps the choice when the link changes to another live branch', async () => {
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();

    typeInto(wrapper, 'repository-live-branch-input', 'release');
    await submitForm(wrapper);

    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(OPTIONAL_FOR_ADMIN);
  });

  it('reports a refusal after the dialog is closed, and leaves the link as it was', async () => {
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="site-id-require"]').trigger('click');
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      (init?.method ?? 'GET') === 'PUT' && String(input).endsWith('/site-id-required')
        ? Promise.resolve(problemResponse(403, 'INSUFFICIENT_ROLE', 'Hiervoor heb je de rol beheerder nodig.'))
        : backend.fetch(input, init),
    );

    await requireDialog(wrapper).find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    // The dialog makes the page inert, so the notification only comes once it is closed.
    expect(requireDialog(wrapper).props('open')).toBe(false);
    const notification = wrapper.find('nldd-notification[variant="critical"]');
    expect(notification.attributes('text')).toBe('Site-ID niet verplicht gemaakt');
    expect(notification.attributes('supporting-text')).toBe('Hiervoor heb je de rol beheerder nodig.');
    expect(requireDialog(wrapper).props('busy')).toBe(false);
    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(OPTIONAL_FOR_ADMIN);
    expect(wrapper.find('[data-testid="site-id-require"]').exists()).toBe(true);
    expect(backend.data.repositories[0]!.siteIdRequired).toBe(false);
  });

  it('reports the generic failure when the request itself does not get through', async () => {
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="site-id-require"]').trigger('click');
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline')));

    await requireDialog(wrapper).find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    const notification = wrapper.find('nldd-notification[variant="critical"]');
    expect(notification.attributes('supporting-text')).toBe('Verplicht maken is niet gelukt.');
    expect(requireDialog(wrapper).props('open')).toBe(false);
  });

  it('closes the dialog when the tab moves to another site, so it cannot act on that one', async () => {
    backend.data.sites.push({ ...backend.data.sites[0]!, id: 'tweede-id', slug: 'docs', title: 'Docs' });
    backend.data.repositories.push({ ...backend.data.repositories[0]!, siteSlug: 'docs', siteId: 'tweede-id' });
    const bodies = recordRepositoryPuts();
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="site-id-require"]').trigger('click');
    expect(requireDialog(wrapper).props('open')).toBe(true);

    await wrapper.setProps({ site: 'docs' });
    await untilIdle();

    expect(requireDialog(wrapper).props('open')).toBe(false);
    expect(bodies).toEqual([]);
    expect(backend.data.repositories.map((link) => link.siteIdRequired)).toEqual([false, false]);
  });

  it('says the same in English', async () => {
    _setLocaleForTest('en');
    stubClipboard();
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-block"]').text()).toContain('Site ID');
    expect(wrapper.find('[data-testid="site-id-copy"]').attributes('text')).toBe('Copy site ID');
    await wrapper.find('[data-testid="site-id-copy"]').trigger('click');
    await untilIdle();
    expect(wrapper.find('[data-testid="site-id-copy-notice"]').text()).toBe('Site ID copied.');
    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(
      "This link also accepts workflows that do not name the site ID. Put site-id in your workflow, then choose 'Only publish with the site ID'.",
    );
    expect(wrapper.find('[data-testid="site-id-require"]').attributes('text')).toBe(
      'Only publish with the site ID',
    );

    await wrapper.find('[data-testid="site-id-require"]').trigger('click');
    const dialog = requireDialog(wrapper);
    expect(dialog.props('title')).toBe('Only publish with the site ID from now on?');
    expect(dialog.props('text')).toBe(
      'Workflows without "site-id" can no longer publish to this site after this. You cannot undo this.',
    );
    expect(dialog.props('keepLabel')).toBe('Keep the current link');
    expect(dialog.props('confirmLabel')).toBe('Only with site ID');

    await dialog.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();
    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(
      'Only workflows that name the site ID can publish.',
    );
  });

  it('says to a member who cannot change it who can, in English too', async () => {
    _setLocaleForTest('en');
    backend.data.loggedInMemberId = 'lid-3';
    stubClipboard(true);

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="site-id-requirement"]').text()).toBe(
      'This link also accepts workflows that do not name the site ID. Only an admin of this site can restrict that.',
    );
    await wrapper.find('[data-testid="site-id-copy"]').trigger('click');
    await untilIdle();
    expect(wrapper.find('[data-testid="site-id-copy-notice"]').text()).toBe(
      'Copying did not work. Select the site ID above and copy it yourself.',
    );
  });

  it('reports a failed request in English too', async () => {
    _setLocaleForTest('en');
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="site-id-require"]').trigger('click');
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline')));

    await requireDialog(wrapper).find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    const notification = wrapper.find('nldd-notification[variant="critical"]');
    expect(notification.attributes('text')).toBe('Site ID not required');
    expect(notification.attributes('supporting-text')).toBe('Requiring the site ID did not work.');
  });
});

describe('TabDeploy: why this is safe', () => {
  it('is present and closed by default, with a summary that names the question', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const block = wrapper.find('[data-testid="deploy-safety"]');
    expect(block.element.tagName.toLowerCase()).toBe('details');
    expect(block.attributes('open')).toBeUndefined();
    expect(block.find('summary').text()).toBe('Waarom is dit veilig?');
  });

  it('expands to show the facts on click', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const block = wrapper.find('[data-testid="deploy-safety"]');
    await block.find('summary').trigger('click');

    const text = block.text();
    expect(text).toContain('Er komt geen geheim in de repository');
    expect(text).toContain('nooit vanuit een pull request');
    expect(text).toContain('raken de live site nooit');
    expect(text).toContain('auditlog');
    expect(text).toContain('stopt publiceren vanuit CI meteen');
  });

  it('shows the same section in English', async () => {
    _setLocaleForTest('en');
    const wrapper = makeWrapper();
    await untilIdle();

    const block = wrapper.find('[data-testid="deploy-safety"]');
    expect(block.find('summary').text()).toBe('Why is this safe?');
    expect(block.text()).toContain('No secret is stored in the repository');
    expect(block.text()).toContain('never touch the live site');

    _setLocaleForTest('nl');
  });
});

describe('TabDeploy: admin status', () => {
  it('shows the read-only view without admin controls when no one is logged in', async () => {
    backend.data.loggedInMemberId = null;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-name"]').text()).toContain('GitHub - team-aurora/website');
    expect(wrapper.find('[data-testid="repository-change"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-unlink"]').exists()).toBe(false);
  });

  it('grants admin controls through an effective site role, not only platform or group admin', async () => {
    // lid-4 (Zoë de Wit) is a group reader in team-aurora, but holds an explicit
    // "admin" site role on team-aurora/website, so her effective role there is admin.
    backend.data.loggedInMemberId = 'lid-4';

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-change"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="repository-unlink"]').exists()).toBe(true);
  });

  it('shows the read-only view, not an empty block, when an open form moves to a site where the member is not admin', async () => {
    // lid-4 is admin on team-aurora/website through a site role, but only a
    // group reader on any other site in team-aurora.
    backend.data.loggedInMemberId = 'lid-4';
    backend.data.sites.push({ ...backend.data.sites[0]!, slug: 'docs', title: 'Docs' });

    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(true);

    await wrapper.setProps({ site: 'docs' });
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-change"]').exists()).toBe(false);
    const empty = wrapper.find('[data-testid="repository-empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.attributes('supporting-text')).toContain('Vraag een beheerder van deze site');
  });

  it('closes an open change form when the tab moves to another site, so it cannot submit to that site', async () => {
    // The default logged-in user (lid-1) is a platform admin, so admin on both.
    backend.data.sites.push({ ...backend.data.sites[0]!, slug: 'docs', title: 'Docs' });

    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(true);

    await wrapper.setProps({ site: 'docs' });
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-link"]').exists()).toBe(true);
  });
});

describe('TabDeploy: owner/repo input without a detail payload', () => {
  it('falls back to the input element value when the field fires a plain input event', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    const field = wrapper.find('[data-testid="repository-owner-repo"]').element as HTMLInputElement;
    field.value = 'minbzk/website';
    field.dispatchEvent(new Event('input'));
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]).toMatchObject({ owner: 'minbzk', repo: 'website' });
  });

  it('falls back to an empty value when neither a detail payload nor the element carries one', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    wrapper.find('[data-testid="repository-owner-repo"]').element.dispatchEvent(new Event('input'));
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.find('[data-testid="repository-owner-repo"]').attributes('invalid')).toBeDefined();
  });
});

describe('TabDeploy: repository reference edge cases', () => {
  it('shows a configured Forgejo host verbatim when it is not a parseable URL', async () => {
    backend.data.repositories = [];
    vi.stubGlobal('fetch', withMeOverride({ ciForgejoHosts: ['not a valid url'] }));

    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'https://gitlab.com/minbzk/website',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.html()).toContain('not a valid url');
  });

  it('refuses a pasted URL with no owner and repository in its path', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'https://github.com/onlyowner',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.html()).toContain('De URL bevat geen eigenaar en repository.');
  });

  it('refuses an unparsable pasted URL', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-owner-repo"]').element, 'input', {
      value: 'https://[',
    });
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.html()).toContain('Dit is geen geldige URL.');
  });
});

describe('TabDeploy: Forgejo host selection while editing', () => {
  it('prefills the configured host when editing an existing Forgejo link, and sends it along again', async () => {
    backend.data.repositories[0]!.provider = 'forgejo';
    backend.data.repositories[0]!.host = 'https://code.overheid.nl';

    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();

    expect(
      (wrapper.find('[data-testid="repository-host"]').element as HTMLSelectElement).value,
    ).toBe('https://code.overheid.nl');

    await wrapper.find('[data-testid="repository-host"]').setValue('https://code.overheid.nl');
    await wrapper.find('[data-testid="repository-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]).toMatchObject({
      provider: 'forgejo',
      host: 'https://code.overheid.nl',
    });
  });

  it('leaves the host blank when editing a GitHub link and no Forgejo host is configured', async () => {
    vi.stubGlobal('fetch', withMeOverride({ ciForgejoHosts: [] }));

    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();

    const providerSelect = wrapper.find('[data-testid="repository-provider"]');
    await providerSelect.setValue('forgejo');
    await untilIdle();

    const hostSelect = wrapper.find('[data-testid="repository-host"]');
    expect(hostSelect.exists()).toBe(true);
    expect(hostSelect.findAll('option')).toHaveLength(0);
  });

  it('opens the link form with no host to pick when no Forgejo host is configured', async () => {
    backend.data.repositories = [];
    vi.stubGlobal('fetch', withMeOverride({ ciForgejoHosts: [] }));

    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-link"]').trigger('click');
    await untilIdle();

    const providerSelect = wrapper.find('[data-testid="repository-provider"]');
    await providerSelect.setValue('forgejo');
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-host"]').findAll('option')).toHaveLength(0);
  });

  it('leaves the live branch field empty when editing a repository without a live branch restriction', async () => {
    backend.data.repositories[0]!.liveBranch = null;

    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-change"]').trigger('click');
    await untilIdle();

    expect(
      (wrapper.find('[data-testid="repository-live-branch-input"]').element as HTMLInputElement).getAttribute(
        'value',
      ),
    ).toBe('');
  });
});

describe('TabDeploy: unlinking and configuration fallbacks', () => {
  it('keeps the repository linked when the unlink confirmation is cancelled', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-unlink"]').trigger('click');
    await untilIdle();
    await wrapper.find('[data-testid="confirm-cancel"]').trigger('click');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(1);
    expect(wrapper.find('[data-testid="repository-name"]').exists()).toBe(true);
  });

  it('reports unlinking failure with the generic message for a non-ApiError failure', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-unlink"]').trigger('click');
    await untilIdle();
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    const notification = wrapper.find('nldd-notification[variant="critical"]');
    expect(notification.exists()).toBe(true);
    expect(notification.attributes('supporting-text')).toBe('Ontkoppelen is niet gelukt.');
  });

  it('falls back to window.location.origin in the snippets when no CI audience is configured', async () => {
    vi.stubGlobal('fetch', withMeOverride({ ciAudience: null }));

    const wrapper = makeWrapper();
    await untilIdle();

    const snippet = wrapper.find('[data-testid="cli-snippet"]').text();
    expect(snippet).toContain(`plak login --host ${window.location.origin}`);
  });

  it('shows "onbekend" as who linked it when the repository has no recorded creator', async () => {
    backend.data.repositories[0]!.createdBy = '';

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.html()).toContain('Gekoppeld door onbekend');
  });
});
