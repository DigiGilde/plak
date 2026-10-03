import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { expectNoAxeViolations } from '../../tests/a11y';
import { MOCK_CONTENT_BASE } from '../api/mock';
import { _resetCurrentMemberCache } from '../composables/currentMember';
import Landing from './Landing.vue';

let wrapper: ReturnType<typeof mount> | null = null;

beforeEach(() => {
  _resetCurrentMemberCache();
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.unstubAllGlobals();
});

// nldd-* elements (Lit) only reflect properties to DOM attributes after their
// own (micro)task update; wait a moment before selecting on attributes, or the
// element is still empty.
async function letUpdateLand(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

const WITHDRAWN = 'Je toegang is ingetrokken door een platformbeheerder.';

/** `/me` with the requested outcome; another route does not touch this test. */
function meFetch(status: number): typeof fetch {
  return () => {
    if (status === 200) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            id: 'lid-9',
            ssoSubject: 'vrijgegeven',
            email: 'lid@voorbeeld.nl',
            name: 'Lid Terug',
            platformRole: 'member',
            status: 'active',
            createdAt: '2026-01-01T00:00:00Z',
            lastLoginAt: null,
            contentBaseUrl: MOCK_CONTENT_BASE,
          }),
          { status: 200, headers: { 'content-type': 'application/json' } },
        ),
      );
    }
    const detail = status === 403 ? WITHDRAWN : 'Er is geen actieve sessie.';
    return Promise.resolve(
      new Response(JSON.stringify({ type: 'about:blank', title: 'Fout', status, detail }), {
        status,
        headers: { 'content-type': 'application/problem+json' },
      }),
    );
  };
}

function makeWithdrawn() {
  return mount(Landing, {
    props: { state: 'awaiting-activation', reason: WITHDRAWN },
    attachTo: document.body,
  });
}

async function clickCheck(): Promise<void> {
  await wrapper!.find('[data-testid="check-again"]').trigger('click');
  await flushPromises();
  await letUpdateLand();
}

describe('Landing: no session', () => {
  it('explains what Plak is and offers a login button', async () => {
    wrapper = mount(Landing, { attachTo: document.body });
    await letUpdateLand();

    expect(wrapper.find('nldd-title h1').text()).toBe('Plak');
    expect(wrapper.text()).toContain('Snel en eenvoudig een HTML-pagina delen');
    expect(wrapper.text()).toContain('Log in met je Rijksoverheid-account om te beginnen.');
    const login = wrapper.find('nldd-button[href="/-/login"]');
    expect(login.exists()).toBe(true);
    expect(login.attributes('variant')).toBe('primary');
  });

  it('explains the steps to share a page and mentions a whole site afterwards', async () => {
    wrapper = mount(Landing, { attachTo: document.body });
    await letUpdateLand();

    const headings = wrapper.findAll('h2').map((h) => h.text());
    expect(headings).toEqual(['Zo deel je een pagina', 'Ook voor een hele site']);
    expect(wrapper.find('ol').findAll('li')).toHaveLength(4);
    expect(wrapper.text()).toContain('oude versies blijven beschikbaar');
    expect(wrapper.text()).toContain('preview per pull request');
  });

  it('has no axe violations', async () => {
    wrapper = mount(Landing, { attachTo: document.body });
    await letUpdateLand();

    await expectNoAxeViolations(wrapper.element);
  });
});

describe('Landing: access withdrawn', () => {
  it('says you are logged in but no longer let in, without a login button', async () => {
    wrapper = makeWithdrawn();
    await letUpdateLand();

    expect(wrapper.find('nldd-title h1').text()).toBe('Je toegang is ingetrokken');
    expect(wrapper.text()).toContain('Je bent ingelogd met je organisatieaccount');
    expect(wrapper.text()).toContain('niet meer');
    expect(wrapper.find('[data-testid="reason"]').text()).toBe(WITHDRAWN);
    expect(wrapper.text()).toContain('Je account staat er nog');
    expect(wrapper.find('nldd-button[href="/-/login"]').exists()).toBe(false);
  });

  it('omits the reason line when the backend does not send one', async () => {
    wrapper = mount(Landing, {
      props: { state: 'awaiting-activation' },
      attachTo: document.body,
    });
    await letUpdateLand();

    expect(wrapper.find('[data-testid="reason"]').exists()).toBe(false);
    expect(wrapper.text()).toContain('Je bent ingelogd met je organisatieaccount');
  });

  it('has no axe violations', async () => {
    wrapper = makeWithdrawn();
    await letUpdateLand();

    await expectNoAxeViolations(wrapper.element);
  });

  it('reports the new session once access is restored', async () => {
    vi.stubGlobal('fetch', meFetch(200));
    wrapper = makeWithdrawn();
    await letUpdateLand();

    await clickCheck();

    expect(wrapper.emitted('refreshed')?.[0]?.[0]).toMatchObject({ state: 'active' });
    expect(wrapper.find('[data-testid="notice-failed"]').exists()).toBe(false);
  });

  it('reports it when access is still withdrawn, without an extra notice', async () => {
    vi.stubGlobal('fetch', meFetch(403));
    wrapper = makeWithdrawn();
    await letUpdateLand();

    await clickCheck();

    expect(wrapper.emitted('refreshed')?.[0]?.[0]).toMatchObject({
      state: 'awaiting-activation',
      reason: WITHDRAWN,
    });
    expect(wrapper.find('[data-testid="notice-failed"]').exists()).toBe(false);
  });

  it('reports an expired session', async () => {
    vi.stubGlobal('fetch', meFetch(401));
    wrapper = makeWithdrawn();
    await letUpdateLand();

    await clickCheck();

    expect(wrapper.emitted('refreshed')?.[0]?.[0]).toMatchObject({ state: 'no-session' });
  });

  it('reports it when the check itself fails', async () => {
    vi.stubGlobal('fetch', meFetch(500));
    wrapper = makeWithdrawn();
    await letUpdateLand();

    await clickCheck();

    expect(wrapper.emitted('refreshed')).toBeUndefined();
    expect(wrapper.find('[data-testid="notice-failed"]').exists()).toBe(true);
  });
});
