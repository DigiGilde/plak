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
    expect(snippet).toContain('minbzk/plak/actions/publiceer@<commit-sha>');
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
    expect(snippet).toContain('uses: https://github.com/minbzk/plak/actions/publiceer@<commit-sha>');
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
    expect(cliLink.attributes('href')).toBe('https://github.com/minbzk/plak');

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
