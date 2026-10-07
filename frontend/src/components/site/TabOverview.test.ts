import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
import ConfirmModal from '@/components/ConfirmModal.vue';
import TabOverview from './TabOverview.vue';
import { serverErrorFetch, untilIdle, fireDetailEvent } from './testHelpers';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeWrapper() {
  return mount(TabOverview, {
    props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
    global: { stubs: { teleport: true } },
  });
}

/** Picks a file in nldd-file-field: the value arrives in `event.detail`. */
function chooseFile(wrapper: ReturnType<typeof makeWrapper>, file: File): void {
  fireDetailEvent(wrapper.find('[data-testid="upload-input"]').element, 'change', {
    files: [file],
  });
}

describe('TabOverview: states', () => {
  it('shows the status and the public URL, and states visibility only once', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.text()).toContain('Status');
    // On the content host, not on the admin origin the SPA itself runs on.
    const url = wrapper.find('[data-testid="public-url"]');
    expect(url.attributes('href')).toBe('https://sites.plak.test/team-aurora/website/');
    expect(url.attributes('href')).not.toContain(window.location.origin);
    expect(wrapper.html()).toContain('Laatste deploy:');

    // Two tags side by side: whether it is live, and who may look.
    const tags = wrapper.findAll('nldd-tag');
    expect(tags).toHaveLength(2);
    expect(tags[0]!.attributes('text')).toBe('Live');
    expect(tags[1]!.attributes('data-testid')).toBe('visibility-tag');
  });

  it("doesn't repeat the deploy history: that lives on the Versies tab", async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.text()).not.toContain('Recente deploys');
    expect(wrapper.html()).not.toContain('Live-deploy');
    expect(wrapper.html()).not.toContain('Huidige live-versie');
  });

  it('gives the address a block with copy and open buttons', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="copy-address"]').attributes('text')).toBe('Kopieer adres');
    const openButtons = wrapper.find('[data-testid="open-site"]');
    expect(openButtons.attributes('href')).toBe('https://sites.plak.test/team-aurora/website/');
    expect(openButtons.attributes('target')).toBe('_blank');
    // The address sits in a block of its own, not as a line among the rest.
    expect(wrapper.findAll('nldd-box').length).toBe(1);
  });

  it('no longer holds the danger zone: deleting the site moved to the Instellingen tab', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.text()).not.toContain('Gevarenzone');
    expect(wrapper.find('nldd-box[background="critical"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="delete-site"]').exists()).toBe(false);
    expect(wrapper.findComponent(ConfirmModal).exists()).toBe(false);
  });

  it('calls the address public as long as it is public', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="address-label"]').text()).toBe('Publieke URL');
    expect(wrapper.find('[data-testid="visible-to"]').text()).toContain(
      'Iedereen kan de site bekijken',
    );
  });

  it('lets the label follow along as soon as something is restricted', async () => {
    backend.data.sites[0]!.access = { base: 'site_team', keys: false, invitees: false };
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="address-label"]').text()).toBe('Adres van je site');
    const who = wrapper.find('[data-testid="visible-to"]').text();
    expect(who).toContain('Alleen wie een rol heeft op deze site');
  });

  it('copies the address and confirms it next to the button', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true });
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="copy-address"]').trigger('click');
    await untilIdle();

    expect(write).toHaveBeenCalledWith('https://sites.plak.test/team-aurora/website/');
    const notice = wrapper.find('[data-testid="copy-notice"]');
    expect(notice.text()).toBe('Adres gekopieerd.');
    // A status line in the page, so no remount swallows the confirmation.
    expect(notice.attributes('role')).toBe('status');
  });

  it('points to the address itself when clipboard access is refused', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockRejectedValue(new Error('geen toestemming')) },
      configurable: true,
    });
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="copy-address"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="copy-notice"]').text()).toContain('Selecteer het adres');
  });

  it('shows the empty state without a live version', async () => {
    backend.data.sites[0]!.hasLiveVersion = false;
    backend.data.sites[0]!.liveVersionId = null;
    backend.data.sites[0]!.lastPublishedAt = null;
    backend.data.versions = [];

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.html()).toContain('Nog geen live versie');
    expect(wrapper.find('[data-testid="public-url"]').exists()).toBe(false);
  });

  it('shows an error message on a server error', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.html()).toContain('Serverfout');
  });

  it('shows a 404 when the site vanished from its group between the two requests', async () => {
    // A real race (deleted from another tab just as this one loads): the
    // versions call still answers, but the group listing no longer has the
    // site, so `found` in TabOverview's load() comes back undefined.
    const realFetch = backend.fetch;
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await realFetch(input, init);
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/-/api/v1/groups/team-aurora') {
        const body = await response.clone().json();
        body.sites = body.sites.filter((entry: { slug: string }) => entry.slug !== 'website');
        return new Response(JSON.stringify(body), {
          status: response.status,
          headers: response.headers,
        });
      }
      return response;
    });

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.html()).toContain('Onbekende site');
  });

  it('shows a 404 message for an unknown site', async () => {
    const wrapper = mount(TabOverview, {
      props: { group: 'team-aurora', site: 'bestaat-niet', contentBase: MOCK_CONTENT_BASE },
    });
    await untilIdle();

    expect(wrapper.html()).toContain('Onbekende site');
  });
});

describe('TabOverview: upload', () => {
  it('chooses a file in the nldd-file-field with the correct accept list', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const field = wrapper.find('[data-testid="upload-input"]');
    expect(field.element.tagName.toLowerCase()).toBe('nldd-file-field');
    expect(field.attributes('accept')).toBe('.zip,.tar.gz,.tgz,.html');
    expect(field.attributes('required')).toBeDefined();
    // nldd-form-field links label and validation list to direct children only.
    expect(field.element.parentElement?.tagName.toLowerCase()).toBe('nldd-form-field');
  });

  it('publishes the chosen file only on the explicit action', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const countBefore = backend.data.versions.length;
    const file = new File(['<html></html>'], 'dist.zip', { type: 'application/zip' });
    chooseFile(wrapper, file);
    await untilIdle();

    // Picking alone publishes nothing.
    expect(backend.data.versions.length).toBe(countBefore);

    await wrapper.find('[data-testid="upload-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.versions.length).toBe(countBefore + 1);
    expect(wrapper.find('nldd-notification[text="Versie gepubliceerd"]').exists()).toBe(true);
    expect(wrapper.emitted('changed')).toBeTruthy();
  });

  it('publishes nothing without a chosen file', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const countBefore = backend.data.versions.length;
    await wrapper.find('[data-testid="upload-form"]').trigger('submit');
    await untilIdle();

    expect(backend.data.versions.length).toBe(countBefore);
  });

  it('shows the error when the upload is refused', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const file = new File(['x'], 'dist.zip');
    chooseFile(wrapper, file);
    // Without a bestand field the backend refuses with a 422; simulate that by
    // letting the fetch return a problem for a moment.
    vi.stubGlobal('fetch', serverErrorFetch());
    await wrapper.find('[data-testid="upload-form"]').trigger('submit');
    await untilIdle();

    expect(wrapper.html()).toContain('Serverfout');
    expect(wrapper.find('nldd-notification[text="Versie gepubliceerd"]').exists()).toBe(false);
  });
});
