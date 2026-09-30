import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
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
    props: { group: 'nldd', site: 'website', contentBase: MOCK_CONTENT_BASE },
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

    expect(wrapper.find('[data-testid="repository-naam"]').text()).toContain(
      'GitHub - nldd/website',
    );
    expect(wrapper.find('[data-testid="repository-livebranch"]').text()).toContain('main');
    expect(wrapper.html()).toContain('Bea Heerder');
  });

  it('shows "elke branch" when there is no live branch restriction', async () => {
    backend.data.repositories[0]!.liveBranch = null;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-livebranch"]').text()).toContain('elke branch');
  });

  it('shows the empty state and the link form without a linked repository', async () => {
    backend.data.repositories = [];

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-leeg"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="repository-formulier"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="workflow-snippet"]').exists()).toBe(false);

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-formulier"]').exists()).toBe(true);
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

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    // No slash: refused client-side, nothing submitted.
    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'ongeldig',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.find('[data-testid="repository-eigenaar-repo"]').attributes('invalid')).toBeDefined();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'minbzk/website',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(1);
    expect(backend.data.repositories[0]).toMatchObject({
      groupSlug: 'nldd',
      siteSlug: 'website',
      provider: 'github',
      owner: 'minbzk',
      repo: 'website',
      // Live-branch is not prefilled; an untouched field means every branch
      // may publish live.
      liveBranch: null,
    });
    expect(wrapper.find('[data-testid="repository-formulier"]').exists()).toBe(false);
    expect(wrapper.find('nldd-notification[variant="success"]').exists()).toBe(true);
  });

  it('fills in a host from ciForgejoHosts for Forgejo and sends it along', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    const providerSelect = wrapper.find('[data-testid="repository-provider"]');
    await providerSelect.setValue('forgejo');
    await untilIdle();

    const hostSelect = wrapper.find('[data-testid="repository-host"]');
    expect(hostSelect.exists()).toBe(true);
    expect(hostSelect.findAll('option').map((o) => o.element.value)).toEqual([
      'https://code.overheid.nl',
    ]);

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'robbertbos/waggle',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
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

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'minbzk/website',
    });
    fireDetailEvent(wrapper.find('[data-testid="repository-livebranch-invoer"]').element, 'input', {
      value: '',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]!.liveBranch).toBeNull();
  });

  it('starts the link form with an empty live branch field, not prefilled with main', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    expect(
      (wrapper.find('[data-testid="repository-livebranch-invoer"]').element as HTMLInputElement).getAttribute(
        'value',
      ),
    ).toBe('');
  });

  it('sends an explicitly filled in live branch along', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'minbzk/website',
    });
    fireDetailEvent(wrapper.find('[data-testid="repository-livebranch-invoer"]').element, 'input', {
      value: 'main',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]!.liveBranch).toBe('main');
  });

  it('recognizes a pasted GitHub URL and derives provider and owner/repo', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'https://github.com/minbzk/website.git',
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-herkend"]').text()).toContain(
      'Herkend: GitHub, minbzk/website',
    );
    expect(
      (wrapper.find('[data-testid="repository-provider"]').element as HTMLSelectElement).value,
    ).toBe('github');

    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
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

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'https://code.overheid.nl/robbertbos/waggle/tree/main',
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-herkend"]').text()).toContain(
      'Herkend: Forgejo (code.overheid.nl), robbertbos/waggle',
    );
    expect(
      (wrapper.find('[data-testid="repository-provider"]').element as HTMLSelectElement).value,
    ).toBe('forgejo');
    expect(
      (wrapper.find('[data-testid="repository-host"]').element as HTMLSelectElement).value,
    ).toBe('https://code.overheid.nl');

    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
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

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'git@github.com:minbzk/website.git',
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-herkend"]').text()).toContain(
      'Herkend: GitHub, minbzk/website',
    );
  });

  it('refuses a URL with an unknown host, with a clear message', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'https://gitlab.com/minbzk/website',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.find('[data-testid="repository-eigenaar-repo"]').attributes('invalid')).toBeDefined();
    expect(wrapper.html()).toContain('Onbekende host "gitlab.com"');
    expect(wrapper.html()).toContain('github.com');
    expect(wrapper.html()).toContain('code.overheid.nl');
  });

  it('still just accepts "owner/repo" without a URL', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'minbzk/website',
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-herkend"]').exists()).toBe(false);

    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]).toMatchObject({ provider: 'github', owner: 'minbzk', repo: 'website' });
  });

  it('opens the form prefilled on Change and updates the link', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
    await untilIdle();

    expect(
      (wrapper.find('[data-testid="repository-eigenaar-repo"]').element as HTMLInputElement).getAttribute(
        'value',
      ),
    ).toBe('nldd/website');

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'nldd/nieuwe-website',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]!.repo).toBe('nieuwe-website');
    expect(wrapper.find('[data-testid="repository-naam"]').text()).toContain('nieuwe-website');
  });

  it('closes the form on cancel without saving anything', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
    await untilIdle();
    await wrapper.find('[data-testid="repository-annuleren"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-formulier"]').exists()).toBe(false);
    expect(backend.data.repositories[0]!.repo).toBe('website');
  });

  it('refuses an empty owner/repo client-side, without a request to the server', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: '',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    // Empty after the slash: refused client-side before the request goes out.
    expect(wrapper.find('[data-testid="repository-eigenaar-repo"]').attributes('invalid')).toBeDefined();
  });

  it('shows the server error message for a failed link', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
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
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-formulier"]').exists()).toBe(true);
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
  await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
  await untilIdle();
}

function typeInto(wrapper: ReturnType<typeof makeWrapper>, testid: string, value: string): void {
  fireDetailEvent(wrapper.find(`[data-testid="${testid}"]`).element, 'input', { value });
}

async function openLinkForm(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
  await untilIdle();
  await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
  await untilIdle();
}

describe('TabDeploy: a repository Plak cannot look up', () => {
  it('offers the id fields only after the lookup failed, then links with the entered ids', async () => {
    backend.data.repositories = [];
    const bodies = recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    expect(wrapper.find('[data-testid="repository-id"]').exists()).toBe(false);

    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/prive-site');
    await submitForm(wrapper);

    expect(bodies[0]).not.toHaveProperty('repositoryId');
    expect(wrapper.html()).toContain('vul dan het repository-id en het eigenaar-id zelf in');
    expect(wrapper.find('[data-testid="repository-ids-uitleg"] code').text()).toBe(
      "gh api repos/nldd/prive-site --jq '.id, .owner.id'",
    );
    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe('');

    typeInto(wrapper, 'repository-id', ' 5005 ');
    typeInto(wrapper, 'repository-eigenaar-id', '6006');
    await submitForm(wrapper);

    expect(bodies[1]).toMatchObject({ owner: 'nldd', repo: 'prive-site', repositoryId: 5005, ownerId: 6006 });
    expect(backend.data.repositories[0]).toMatchObject({ repo: 'prive-site', repositoryId: 5005, ownerId: 6006 });
    expect(wrapper.find('[data-testid="repository-formulier"]').exists()).toBe(false);
  });

  it('sends no ids while both fields stay empty, and keeps the fields in view', async () => {
    backend.data.repositories = [];
    const bodies = recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/prive-site');
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
    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/prive-site');
    await submitForm(wrapper);

    typeInto(wrapper, 'repository-id', repositoryId);
    typeInto(wrapper, 'repository-eigenaar-id', ownerId);
    await submitForm(wrapper);

    expect(bodies).toHaveLength(1);
    expect(wrapper.find('[data-testid="repository-id"]').attributes('invalid')).toBeDefined();
    expect(wrapper.find('[data-testid="repository-eigenaar-id"]').attributes('unmet')).toBe('repository-ids-fout');
    expect(wrapper.find('#repository-ids-fout').text()).toBe(
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
    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/website');
    await submitForm(wrapper);
    typeInto(wrapper, 'repository-id', '1');
    typeInto(wrapper, 'repository-eigenaar-id', '2');
    await submitForm(wrapper);

    expect(wrapper.find('#repository-ids-fout').text()).toBe('GitHub geeft andere ids.');
    expect(wrapper.find('#repository-server').text()).toBe('');
    expect(wrapper.find('[data-testid="repository-eigenaar-repo"]').attributes('invalid')).toBeUndefined();
  });

  it('keeps the command on the repository it failed for while the input is mid-edit', async () => {
    backend.data.repositories = [];
    recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/prive-site');
    await submitForm(wrapper);

    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-ids-uitleg"] code').text()).toContain('repos/nldd/prive-site');

    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/prive-docs');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-ids-uitleg"] code').text()).toContain('repos/nldd/prive-docs');
  });

  it('names the Forgejo API for a Forgejo repository', async () => {
    backend.data.repositories = [];
    recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    await wrapper.find('[data-testid="repository-provider"]').setValue('forgejo');
    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/prive-site');
    await submitForm(wrapper);

    expect(wrapper.find('[data-testid="repository-ids-uitleg"] code').text()).toBe(
      `curl -s -H "Authorization: token <token>" https://code.overheid.nl/api/v1/repos/nldd/prive-site | jq '.id, .owner.id'`,
    );
  });

  it('prefills the stored ids when a linked repository turns out private on Change', async () => {
    const linked = backend.data.repositories[0]!;
    const bodies = recordRepositoryPuts(notFoundWithoutIds);
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
    await untilIdle();
    typeInto(wrapper, 'repository-eigenaar-repo', `${linked.owner}/${linked.repo}`.toUpperCase());
    typeInto(wrapper, 'repository-livebranch-invoer', 'release');
    await submitForm(wrapper);

    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe(String(linked.repositoryId));
    expect(wrapper.find('[data-testid="repository-eigenaar-id"]').attributes('value')).toBe(String(linked.ownerId));

    // Another spelling of the same repository keeps them.
    typeInto(wrapper, 'repository-eigenaar-repo', `https://github.com/${linked.owner}/${linked.repo}`);
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe(String(linked.repositoryId));

    await submitForm(wrapper);
    expect(bodies[1]).toMatchObject({
      repositoryId: linked.repositoryId,
      ownerId: linked.ownerId,
      liveBranch: 'release',
    });
    expect(wrapper.find('[data-testid="repository-livebranch"]').text()).toContain('release');
  });

  it('prefills the stored ids of a linked Forgejo repository on the same host', async () => {
    backend.data.repositories = [
      { ...backend.data.repositories[0]!, provider: 'forgejo', host: 'https://code.overheid.nl' },
    ];
    recordRepositoryPuts(notFoundWithoutIds);
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
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
    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
    await untilIdle();
    await submitForm(wrapper);
    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe(String(linked.repositoryId));

    typeInto(wrapper, 'repository-eigenaar-repo', `${linked.owner}/${linked.repo}-oud`);
    await untilIdle();
    typeInto(wrapper, 'repository-eigenaar-repo', `${linked.owner}/${linked.repo}`);
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe('');
    expect(wrapper.find('[data-testid="repository-eigenaar-id"]').attributes('value')).toBe('');
    await submitForm(wrapper);
    expect(bodies[1]).not.toHaveProperty('repositoryId');
  });

  it('drops prefilled ids when the provider changes, but keeps ids typed by hand', async () => {
    recordRepositoryPuts(notFoundWithoutIds);
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
    await untilIdle();
    await submitForm(wrapper);

    await wrapper.find('[data-testid="repository-provider"]').setValue('forgejo');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe('');

    typeInto(wrapper, 'repository-id', '5005');
    typeInto(wrapper, 'repository-eigenaar-id', '6006');
    await wrapper.find('[data-testid="repository-provider"]').setValue('github');
    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/prive-docs');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe('5005');
  });

  it('does not prefill the stored ids for another repository', async () => {
    recordRepositoryPuts(notFoundWithoutIds);
    const wrapper = makeWrapper();
    await untilIdle();
    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
    await untilIdle();
    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/prive-docs');
    await submitForm(wrapper);

    expect(wrapper.find('[data-testid="repository-id"]').attributes('value')).toBe('');
    expect(wrapper.find('[data-testid="repository-eigenaar-id"]').attributes('value')).toBe('');
  });

  it('shows the generic message and no id fields when the request itself fails', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/prive-site');
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('offline')));
    await submitForm(wrapper);

    expect(wrapper.find('#repository-server').text()).toBe('Koppelen is niet gelukt.');
    expect(wrapper.find('[data-testid="repository-id"]').exists()).toBe(false);
  });

  it('hides the id fields again when the form is reopened', async () => {
    backend.data.repositories = [];
    recordRepositoryPuts();
    const wrapper = makeWrapper();
    await openLinkForm(wrapper);
    typeInto(wrapper, 'repository-eigenaar-repo', 'nldd/prive-site');
    await submitForm(wrapper);
    expect(wrapper.find('[data-testid="repository-id"]').exists()).toBe(true);

    await wrapper.find('[data-testid="repository-annuleren"]').trigger('click');
    await untilIdle();
    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();
    expect(wrapper.find('[data-testid="repository-id"]').exists()).toBe(false);
  });
});

describe('TabDeploy: unlinking the repository', () => {
  it('asks for confirmation and only then unlinks', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-ontkoppelen"]').trigger('click');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(1);

    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.find('[data-testid="repository-leeg"]').exists()).toBe(true);
  });

  it('reports it when unlinking fails', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-ontkoppelen"]').trigger('click');
    vi.stubGlobal('fetch', serverErrorFetch());
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('nldd-notification[variant="critical"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="repository-naam"]').exists()).toBe(true);
  });
});

describe('TabDeploy: visibility by role', () => {
  it('editor sees no link form or buttons, but does see the linked repository and the snippet', async () => {
    // lid-3 (Ada Vermeer) is editor in group nldd, not a site admin.
    backend.data.loggedInMemberId = 'lid-3';

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-formulier"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-wijzigen"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-ontkoppelen"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-koppelen"]').exists()).toBe(false);

    expect(wrapper.find('[data-testid="repository-naam"]').text()).toContain(
      'GitHub - nldd/website',
    );
    expect(wrapper.find('[data-testid="workflow-snippet"]').exists()).toBe(true);
  });

  it('editor without a linked repository sees a reference to the site admin', async () => {
    backend.data.loggedInMemberId = 'lid-3';
    backend.data.repositories = [];

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-formulier"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-koppelen"]').exists()).toBe(false);
    const empty = wrapper.find('[data-testid="repository-leeg"]');
    expect(empty.exists()).toBe(true);
    expect(empty.attributes('supporting-text')).toContain('Vraag een beheerder van deze site');
  });

  it('site admin (platform admin) sees the link form and the buttons', async () => {
    // The default logged-in user (lid-1) is a platform admin.
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-wijzigen"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="repository-ontkoppelen"]').exists()).toBe(true);

    backend.data.repositories = [];
    const emptyWrapper = makeWrapper();
    await untilIdle();

    expect(emptyWrapper.find('[data-testid="repository-koppelen"]').exists()).toBe(true);
    await emptyWrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();
    expect(emptyWrapper.find('[data-testid="repository-formulier"]').exists()).toBe(true);
  });
});

describe('TabDeploy: workflow snippet and curl fallback', () => {
  it('fills the github snippet with host, site and no secrets', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const snippet = wrapper.find('[data-testid="workflow-snippet"]').text();
    expect(snippet).toContain('host: https://plak.test');
    expect(snippet).toContain('site: nldd/website');
    expect(snippet).toContain('DigiGilde/plak/actions/publiceer@<commit-sha>');
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
    expect(snippet).toContain('uses: https://github.com/DigiGilde/plak/actions/publiceer@<commit-sha>');
    expect(snippet).toContain('runs-on: docker');
    expect(snippet).not.toContain('secrets.');
  });

  it('shows the cli commands for login, live, preview and logout with host and site filled in', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const snippet = wrapper.find('[data-testid="cli-snippet"]').text();
    expect(snippet).toContain('plak login --host https://plak.test');
    expect(snippet).toContain('plak publish ./dist --host https://plak.test --site nldd/website');
    expect(snippet).toContain(
      'plak publish ./dist --host https://plak.test --site nldd/website --preview pr-42',
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

  it('names the workflow file path and links to the versions tab', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const richText = wrapper.find('[data-testid="workflow-snippet"]').element.parentElement!;
    expect(richText.textContent).toContain('.github/workflows/publiceer.yml');

    const versionsLink = wrapper.find('[data-testid="deploy-naar-versies"]');
    expect(versionsLink.attributes('href')).toBe('/nldd/website/versions');
    expect(versionsLink.text()).toBe('Versies');
  });

  it('names the Forgejo workflow file path for a Forgejo repository', async () => {
    backend.data.repositories[0]!.provider = 'forgejo';
    backend.data.repositories[0]!.host = 'https://code.overheid.nl';

    const wrapper = makeWrapper();
    await untilIdle();

    const richText = wrapper.find('[data-testid="workflow-snippet"]').element.parentElement!;
    expect(richText.textContent).toContain('.forgejo/workflows/publiceer.yml');
  });
});

describe('TabDeploy: why this is safe', () => {
  it('is present and closed by default, with a summary that names the question', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const block = wrapper.find('[data-testid="deploy-veiligheid"]');
    expect(block.element.tagName.toLowerCase()).toBe('details');
    expect(block.attributes('open')).toBeUndefined();
    expect(block.find('summary').text()).toBe('Waarom is dit veilig?');
  });

  it('expands to show the facts on click', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const block = wrapper.find('[data-testid="deploy-veiligheid"]');
    await block.find('summary').trigger('click');

    const text = block.text();
    expect(text).toContain('Er komt geen geheim in het repository');
    expect(text).toContain('nooit vanuit een pull request');
    expect(text).toContain('raken de live site nooit');
    expect(text).toContain('auditlog');
    expect(text).toContain('stopt publiceren vanuit CI meteen');
  });

  it('shows the same section in English', async () => {
    _setLocaleForTest('en');
    const wrapper = makeWrapper();
    await untilIdle();

    const block = wrapper.find('[data-testid="deploy-veiligheid"]');
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

    expect(wrapper.find('[data-testid="repository-naam"]').text()).toContain('GitHub - nldd/website');
    expect(wrapper.find('[data-testid="repository-wijzigen"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="repository-ontkoppelen"]').exists()).toBe(false);
  });

  it('grants admin controls through an effective site role, not only platform or group admin', async () => {
    // lid-4 (Zoë de Wit) is a group reader in nldd, but holds an explicit
    // "admin" site role on nldd/website, so her effective role there is admin.
    backend.data.loggedInMemberId = 'lid-4';

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="repository-wijzigen"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="repository-ontkoppelen"]').exists()).toBe(true);
  });
});

describe('TabDeploy: owner/repo input without a detail payload', () => {
  it('falls back to the input element value when the field fires a plain input event', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    const field = wrapper.find('[data-testid="repository-eigenaar-repo"]').element as HTMLInputElement;
    field.value = 'minbzk/website';
    field.dispatchEvent(new Event('input'));
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories[0]).toMatchObject({ owner: 'minbzk', repo: 'website' });
  });

  it('falls back to an empty value when neither a detail payload nor the element carries one', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    wrapper.find('[data-testid="repository-eigenaar-repo"]').element.dispatchEvent(new Event('input'));
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.find('[data-testid="repository-eigenaar-repo"]').attributes('invalid')).toBeDefined();
  });
});

describe('TabDeploy: repository reference edge cases', () => {
  it('shows a configured Forgejo host verbatim when it is not a parseable URL', async () => {
    backend.data.repositories = [];
    vi.stubGlobal('fetch', withMeOverride({ ciForgejoHosts: ['not a valid url'] }));

    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'https://gitlab.com/minbzk/website',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.html()).toContain('not a valid url');
  });

  it('refuses a pasted URL with no owner and repository in its path', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'https://github.com/onlyowner',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(0);
    expect(wrapper.html()).toContain('De URL bevat geen eigenaar en repository.');
  });

  it('refuses an unparsable pasted URL', async () => {
    backend.data.repositories = [];
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="repository-eigenaar-repo"]').element, 'input', {
      value: 'https://[',
    });
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
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

    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
    await untilIdle();

    expect(
      (wrapper.find('[data-testid="repository-host"]').element as HTMLSelectElement).value,
    ).toBe('https://code.overheid.nl');

    await wrapper.find('[data-testid="repository-host"]').setValue('https://code.overheid.nl');
    await wrapper.find('[data-testid="repository-formulier"]').trigger('submit');
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

    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
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

    await wrapper.find('[data-testid="repository-koppelen"]').trigger('click');
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

    await wrapper.find('[data-testid="repository-wijzigen"]').trigger('click');
    await untilIdle();

    expect(
      (wrapper.find('[data-testid="repository-livebranch-invoer"]').element as HTMLInputElement).getAttribute(
        'value',
      ),
    ).toBe('');
  });
});

describe('TabDeploy: unlinking and configuration fallbacks', () => {
  it('keeps the repository linked when the unlink confirmation is cancelled', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-ontkoppelen"]').trigger('click');
    await untilIdle();
    await wrapper.find('[data-testid="bevestig-annuleren"]').trigger('click');
    await untilIdle();

    expect(backend.data.repositories).toHaveLength(1);
    expect(wrapper.find('[data-testid="repository-naam"]').exists()).toBe(true);
  });

  it('reports unlinking failure with the generic message for a non-ApiError failure', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="repository-ontkoppelen"]').trigger('click');
    await untilIdle();
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));
    await wrapper.find('[data-testid="bevestig-doorgaan"]').trigger('click');
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
