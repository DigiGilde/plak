import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import { serverErrorFetch, untilIdle } from '@/components/site/testHelpers';
import { _resetBreadcrumbs, breadcrumbsFor } from '@/composables/breadcrumbs';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import { _setLocaleForTest, currentLocale, languageChoice } from '@/i18n';
import Profiel from './Profiel.vue';

let backend: MockBackend;
let router: Router;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  _resetBreadcrumbs();
  _resetCurrentMemberCache();
  router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/-/profile', component: Profiel }],
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  _setLocaleForTest('nl');
});

async function makeWrapper() {
  await router.push('/-/profile');
  await router.isReady();
  const wrapper = mount(Profiel, { global: { plugins: [router], stubs: { teleport: true } } });
  await untilIdle();
  return wrapper;
}

function checked(wrapper: Awaited<ReturnType<typeof makeWrapper>>, testid: string): boolean {
  return wrapper.find(`[data-testid="${testid}"]`).attributes('checked') !== undefined;
}

describe('Profiel: who you are', () => {
  it('names you, your address and your role on the platform', async () => {
    const wrapper = await makeWrapper();

    expect(wrapper.find('h1').text()).toBe('Profiel');
    expect(wrapper.find('[data-testid="profiel-naam"]').html()).toContain('Bea Heerder');
    expect(wrapper.find('[data-testid="profiel-email"]').html()).toContain(
      'beheerder@voorbeeld.nl',
    );
    expect(wrapper.find('[data-testid="profiel-rol"]').html()).toContain('Platformbeheerder');
  });

  it('calls an ordinary member a member', async () => {
    backend.data.loggedInMemberId = 'lid-3';
    const wrapper = await makeWrapper();

    expect(wrapper.find('[data-testid="profiel-rol"]').html()).toContain('Lid');
  });

  it('says so when the identity provider supplied no name', async () => {
    backend.data.members[0]!.name = '   ';
    const wrapper = await makeWrapper();

    expect(wrapper.find('[data-testid="profiel-naam"]').html()).toContain('Onbekend');
  });

  it('supplies the breadcrumb path to the app shell', async () => {
    await makeWrapper();

    expect(breadcrumbsFor('/-/profile')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Profiel' },
    ]);
  });

  it('links to the linked sessions rather than listing them here', async () => {
    const wrapper = await makeWrapper();

    expect(wrapper.find('[data-testid="profiel-sessies"]').attributes('href')).toBe(
      '/-/sessions',
    );
  });
});

describe('Profiel: the language choice', () => {
  it('starts on "follow my browser" when the account holds no choice', async () => {
    const wrapper = await makeWrapper();

    expect(checked(wrapper, 'taal-auto')).toBe(true);
    expect(checked(wrapper, 'taal-nl')).toBe(false);
    expect(checked(wrapper, 'taal-en')).toBe(false);
  });

  it('marks the language the account carries', async () => {
    backend.data.myLanguage = 'en';
    const wrapper = await makeWrapper();

    expect(checked(wrapper, 'taal-en')).toBe(true);
  });

  it('switches the interface and stores the choice on the account', async () => {
    const wrapper = await makeWrapper();

    await wrapper.find('[data-testid="taal-en"]').trigger('change');
    await untilIdle();

    expect(currentLocale.value).toBe('en');
    expect(backend.data.myLanguage).toBe('en');
    expect(wrapper.find('h1').text()).toBe('Profile');
  });

  it('follows through to the html element, for a screen reader', async () => {
    const wrapper = await makeWrapper();

    await wrapper.find('[data-testid="taal-en"]').trigger('change');
    await untilIdle();

    expect(document.documentElement.lang).toBe('en');
  });

  it('hands the choice back to the browser again', async () => {
    backend.data.myLanguage = 'en';
    const wrapper = await makeWrapper();

    await wrapper.find('[data-testid="taal-auto"]').trigger('change');
    await untilIdle();

    expect(backend.data.myLanguage).toBeNull();
    expect(languageChoice.value).toBeNull();
    expect(currentLocale.value).toBe('nl');
  });

  it('confirms the change', async () => {
    const wrapper = await makeWrapper();

    await wrapper.find('[data-testid="taal-en"]').trigger('change');
    await untilIdle();

    const notice = wrapper.find('nldd-notification');
    expect(notice.exists()).toBe(true);
    expect(notice.attributes('text')).toBe('Language saved');
  });

  it('does nothing when the choice that is already on is chosen again', async () => {
    const wrapper = await makeWrapper();

    await wrapper.find('[data-testid="taal-auto"]').trigger('change');
    await untilIdle();

    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('confirms a switch to Dutch in Dutch', async () => {
    backend.data.myLanguage = 'en';
    const wrapper = await makeWrapper();

    await wrapper.find('[data-testid="taal-nl"]').trigger('change');
    await untilIdle();

    expect(currentLocale.value).toBe('nl');
    expect(wrapper.find('nldd-notification').attributes('text')).toBe('Taal opgeslagen');
  });

  it('ignores a second choice while the first is still being saved', async () => {
    // Two clicks in a row would otherwise race, and the second would take the
    // first one's optimistic value as the one to roll back to.
    const wrapper = await makeWrapper();

    const first = wrapper.find('[data-testid="taal-en"]').trigger('change');
    await wrapper.find('[data-testid="taal-nl"]').trigger('change');
    await first;
    await untilIdle();

    expect(backend.data.myLanguage).toBe('en');
  });

  it('puts the language back when the account refuses the change', async () => {
    const wrapper = await makeWrapper();
    vi.stubGlobal('fetch', serverErrorFetch());

    await wrapper.find('[data-testid="taal-en"]').trigger('change');
    await untilIdle();

    expect(currentLocale.value).toBe('nl');
    expect(checked(wrapper, 'taal-auto')).toBe(true);
    expect(wrapper.find('nldd-notification').attributes('text')).toBe('Taal niet opgeslagen');
  });
});
