import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
import type { Access } from '@/api/types';
import TabAccess from './TabAccess.vue';
import { serverErrorFetch, untilIdle, fireDetailEvent } from './testHelpers';

/** A network failure, as opposed to `serverErrorFetch()`'s problem+json: no
 * `ApiError` comes out of this one, so it exercises `errorText`'s fallback. */
function networkErrorFetch(): typeof fetch {
  return () => Promise.reject(new TypeError('network down'));
}

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  // Both extras on by default in these tests, so both tables are on screen;
  // the tests about showing and hiding them set their own value.
  backend.data.sites[0]!.access = { base: 'sso', keys: true, invitees: true };
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function setAccess(access: Access): void {
  backend.data.sites[0]!.access = access;
}

function makeWrapper() {
  return mount(TabAccess, {
    props: { group: 'nldd', site: 'website', contentBase: MOCK_CONTENT_BASE },
    global: { stubs: { teleport: true } },
  });
}

describe('TabAccess: the base', () => {
  it('shows the four base choices as a radio-group list with an explanation per option', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const group = wrapper.find('[data-testid="toegang-basis"]');
    expect(group.element.tagName.toLowerCase()).toBe('nldd-list');
    expect(group.attributes('type')).toBe('radiogroup');

    const rows = group.findAll('nldd-list-item');
    expect(rows).toHaveLength(4);
    expect(rows[0]!.attributes('radio')).toBeDefined();
    expect(wrapper.find('[data-testid="basis-sso"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('[data-testid="basis-public"]').attributes('checked')).toBeUndefined();

    // The radio's shape sits inside the row, its state on the row itself.
    const button = wrapper.find('[data-testid="basis-public"]').find('nldd-radio-button');
    expect(button.attributes('decorative')).toBeDefined();
    expect(button.attributes('checked')).toBeUndefined();

    // Label and explanation sit apart in an nldd-title-cell, not in one string.
    const cell = wrapper.find('[data-testid="basis-public"]').find('nldd-title-cell');
    expect(cell.attributes('text')).toBe('Iedereen');
    expect(cell.attributes('supporting-text')).toContain('De site is openbaar');
    expect(wrapper.find('[data-testid="basis-nobody"]').find('nldd-title-cell').attributes('text')).toBe(
      'Alleen via een link of uitnodiging',
    );
  });

  it('summarizes who can really see the site, base and exceptions combined', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const summary = wrapper.find('[data-testid="toegang-samenvatting"]').attributes('text')!;
    expect(summary).toContain('SSO Rijk');
    expect(summary).toContain('geheime link');
    expect(summary).toContain('genodigden');
  });

  it('says for base "nobody" without exceptions that no one can get in', async () => {
    setAccess({ base: 'nobody', keys: false, invitees: false });
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="toegang-samenvatting"]').attributes('text')).toContain(
      'Niemand kan de site bekijken',
    );
  });

  it('saves a new base together with the exceptions, in a PUT', async () => {
    const calls: string[] = [];
    const real = backend.fetch;
    vi.stubGlobal('fetch', (input: RequestInfo | URL, options?: RequestInit) => {
      if (String(input).endsWith('/access')) calls.push(String(options?.body ?? ''));
      return real(input, options);
    });

    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="basis-site_team"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(calls).toEqual([JSON.stringify({ base: 'site_team', keys: true, invitees: true })]);
    expect(backend.data.sites[0]!.access).toEqual({
      base: 'site_team',
      keys: true,
      invitees: true,
    });
    expect(wrapper.find('nldd-notification[text="Toegang opgeslagen"]').exists()).toBe(true);
    expect(wrapper.emitted('changed')).toBeTruthy();
  });

  it('rolls back the choice and reports it when saving fails', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    fireDetailEvent(wrapper.find('[data-testid="basis-public"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="basis-sso"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('[data-testid="basis-public"]').attributes('checked')).toBeUndefined();
    expect(wrapper.find('nldd-notification[variant="critical"]').attributes('text')).toBe(
      'Toegang niet opgeslagen',
    );
  });

  it('reports the generic failure text when saving does not even reach the server', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', networkErrorFetch());
    fireDetailEvent(wrapper.find('[data-testid="basis-public"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    // errorText's fallback: without an ApiError there is no problem detail or
    // title to show, so the generic saveFailedDetail text carries the notice.
    expect(wrapper.find('nldd-notification[variant="critical"]').attributes('supporting-text')).toBe(
      'Opslaan is niet gelukt.',
    );
  });

  it('does nothing when the radio reports the base it already has', async () => {
    const calls: string[] = [];
    const real = backend.fetch;
    vi.stubGlobal('fetch', (input: RequestInfo | URL, options?: RequestInit) => {
      if (String(input).endsWith('/access')) calls.push(String(options?.body ?? ''));
      return real(input, options);
    });

    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="basis-sso"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(calls).toEqual([]);
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('ignores base, secret link and invitee switches touched before the data has loaded', async () => {
    // No await here: access is still null, so every save function's guard
    // clause has to return early rather than PUT against a site it has not
    // read yet.
    const wrapper = makeWrapper();

    fireDetailEvent(wrapper.find('[data-testid="basis-public"]').element, 'change', {
      checked: true,
    });
    fireDetailEvent(wrapper.find('[data-testid="uitzondering-sleutels"]').element, 'change', {
      checked: false,
    });
    fireDetailEvent(wrapper.find('[data-testid="uitzondering-genodigden"]').element, 'change', {
      checked: false,
    });
    await untilIdle();

    // The backend's seeded access ('sso', keys+invitees on) survived untouched.
    expect(backend.data.sites[0]!.access).toEqual({ base: 'sso', keys: true, invitees: true });
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });
});

/**
 * The help text next to a control, and only there. Written inside the switch
 * it is still in the DOM, so a plain `.text()` or a descendant query reads it
 * either way; what decides whether anyone sees it is being a direct child of
 * the form-field, because nldd-switch-field renders no slot of its own.
 */
function helpTextBeside(control: Element): string {
  const field = control.closest('nldd-form-field');
  expect(field, 'the switch has no nldd-form-field around it').not.toBeNull();
  expect(
    control.querySelector('nldd-form-field-help-text'),
    'the help text sits inside the switch, where it never renders',
  ).toBeNull();
  return field!.querySelector(':scope > nldd-form-field-help-text')?.textContent ?? '';
}

describe('TabAccess: the two exceptions', () => {
  it('offers them as switches beside the base, not as options within it', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const keys = wrapper.find('[data-testid="uitzondering-sleutels"]');
    expect(keys.element.tagName.toLowerCase()).toBe('nldd-switch-field');
    expect(keys.attributes('label')).toBe('Geheime links');
    expect(keys.attributes('checked')).toBeDefined();
    // The hint belongs to the surrounding form-field, not inside the switch:
    // nldd-switch-field renders no slot, so a help text written within it is
    // in the DOM but never on the screen, which jsdom cannot tell apart.
    expect(helpTextBeside(keys.element)).toContain('zonder in te loggen');

    const invitees = wrapper.find('[data-testid="uitzondering-genodigden"]');
    expect(invitees.attributes('label')).toBe('Genodigden');
    expect(invitees.attributes('checked')).toBeDefined();
    expect(helpTextBeside(invitees.element)).toContain('SSO Rijk');
  });

  it('turns on an exception without touching the base', async () => {
    setAccess({ base: 'site_team', keys: false, invitees: false });
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="uitzondering-sleutels"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.access).toEqual({
      base: 'site_team',
      keys: true,
      invitees: false,
    });
    expect(wrapper.find('nldd-notification[text="Geheime links staan aan"]').exists()).toBe(true);
  });

  it('turns off an exception and hides its table', async () => {
    const wrapper = makeWrapper();
    await untilIdle();
    expect(wrapper.find('[data-testid="genodigden-lijst"]').exists()).toBe(true);

    fireDetailEvent(wrapper.find('[data-testid="uitzondering-genodigden"]').element, 'change', {
      checked: false,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.access.invitees).toBe(false);
    expect(wrapper.find('[data-testid="genodigden-lijst"]').exists()).toBe(false);
    // The keys table is untouched: the two extras are independent.
    expect(wrapper.find('[data-testid="sleutels-lijst"]').exists()).toBe(true);
  });

  it('turns off secret links and hides their table', async () => {
    const wrapper = makeWrapper();
    await untilIdle();
    expect(wrapper.find('[data-testid="sleutels-lijst"]').exists()).toBe(true);

    fireDetailEvent(wrapper.find('[data-testid="uitzondering-sleutels"]').element, 'change', {
      checked: false,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.access.keys).toBe(false);
    expect(wrapper.find('[data-testid="sleutels-lijst"]').exists()).toBe(false);
    expect(wrapper.find('nldd-notification[text="Geheime links staan uit"]').exists()).toBe(true);
  });

  it('turns on invitees when it was off, without touching the base', async () => {
    setAccess({ base: 'site_team', keys: true, invitees: false });
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="uitzondering-genodigden"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.access).toEqual({
      base: 'site_team',
      keys: true,
      invitees: true,
    });
    expect(wrapper.find('nldd-notification[text="Genodigden staan aan"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="genodigden-lijst"]').exists()).toBe(true);
  });

  it('does nothing when a switch reports the value it already has', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="uitzondering-sleutels"]').element, 'change', {
      checked: true,
    });
    fireDetailEvent(wrapper.find('[data-testid="uitzondering-genodigden"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.access).toEqual({ base: 'sso', keys: true, invitees: true });
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('shows no tables as long as both exceptions are off', async () => {
    setAccess({ base: 'sso', keys: false, invitees: false });
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="sleutels-lijst"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="genodigden-lijst"]').exists()).toBe(false);
  });

  it('says for a public base that the exceptions add nothing', async () => {
    setAccess({ base: 'public', keys: true, invitees: true });
    const wrapper = makeWrapper();
    await untilIdle();

    const banner = wrapper.find('[data-testid="uitzonderingen-zinloos"]');
    expect(banner.exists()).toBe(true);
    expect(banner.attributes('supporting-text')).toContain('iedereen mag toch al kijken');
    expect(wrapper.find('[data-testid="toegang-samenvatting"]').attributes('text')).toContain(
      'voegen daar niets aan toe',
    );
    // Said, not silently done: the lists stay reachable so what is set up here
    // survives turning the base back down.
    expect(wrapper.find('[data-testid="sleutels-lijst"]').exists()).toBe(true);
  });

  it('omits the notice once the base shields off something', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="uitzonderingen-zinloos"]').exists()).toBe(false);
  });
});

describe('TabAccess: states', () => {
  it('keeps the structure in place while loading, instead of a bare indicator', () => {
    // No await: this is the first render, with the data still in flight.
    const wrapper = makeWrapper();

    const indicator = wrapper.find('nldd-activity-indicator');
    expect(indicator.exists()).toBe(true);
    expect(indicator.attributes('complete')).toBeUndefined();
    expect(indicator.attributes('timing')).toBeUndefined();
    expect(indicator.findAll('[data-testid="toegang-basis"] nldd-list-item')).toHaveLength(4);
    expect(wrapper.html()).toContain('Wie kan deze site bekijken?');
  });

  it('sets the indicator to complete once the data has arrived', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('nldd-activity-indicator').attributes('complete')).toBeDefined();
  });

  it('shows empty states in the tables themselves', async () => {
    backend.data.invitees = [];
    backend.data.keys = [];

    const wrapper = makeWrapper();
    await untilIdle();

    // The empty text belongs in the table's own empty slot, so the table
    // decides when to show it.
    const inviteesEmpty = wrapper.find('[data-testid="genodigden-leeg"]');
    expect(inviteesEmpty.attributes('slot')).toBe('empty');
    expect(inviteesEmpty.element.parentElement?.tagName.toLowerCase()).toBe('nldd-table');
    const keysEmpty = wrapper.find('[data-testid="sleutels-leeg"]');
    expect(keysEmpty.attributes('slot')).toBe('empty');
    expect(keysEmpty.element.parentElement?.tagName.toLowerCase()).toBe('nldd-table');
  });

  it('shows an error message on a server error', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.html()).toContain('Serverfout');
  });

  it('shows a 404 when the site vanished from its group between the two requests', async () => {
    // A real race (deleted from another tab just as this one loads): the
    // invitees and keys calls still answer, but the group listing no longer
    // has the site, so `siteRow` in TabAccess's load() comes back undefined.
    const realFetch = backend.fetch;
    vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
      const response = await realFetch(input, init);
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/-/api/v1/groups/nldd') {
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

    expect(wrapper.html()).toContain('Onbekend site');
  });
});

describe('TabAccess: external sources', () => {
  it('is on by default, with the consequences in the explanation', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const field = wrapper.find('[data-testid="externe-bronnen"]');
    expect(field.element.tagName.toLowerCase()).toBe('nldd-switch-field');
    expect(field.attributes('label')).toBe('Externe bronnen toestaan');
    expect(field.attributes('checked')).toBeDefined();

    const section = wrapper.find('section[aria-labelledby="kop-externe-bronnen"]');
    expect(section.text()).toContain('Staat aan, tenzij je het uitzet');
    expect(section.text()).toContain('cdnjs, jsDelivr en unpkg');
    expect(section.text()).toContain('Tailwind-CDN');
    expect(section.text()).toContain('IP-adres');
    // What stays blocked either way belongs beside the switch, or turning it
    // off looks like the only thing standing between the page and the web.
    expect(section.text()).toContain('iframe');
    expect(section.text()).toContain('formulier');
  });

  it('is off when the site has turned it off', async () => {
    backend.data.sites[0]!.externalSources = false;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="externe-bronnen"]').attributes('checked')).toBeUndefined();
  });

  it('saves turning it off', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="externe-bronnen"]').element, 'change', {
      checked: false,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.externalSources).toBe(false);
    expect(wrapper.find('[data-testid="externe-bronnen"]').attributes('checked')).toBeUndefined();
    expect(wrapper.find('nldd-notification[text="Externe bronnen opgeslagen"]').exists()).toBe(
      true,
    );
  });

  it('rolls back the switch and reports it when saving fails', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    fireDetailEvent(wrapper.find('[data-testid="externe-bronnen"]').element, 'change', {
      checked: false,
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="externe-bronnen"]').attributes('checked')).toBeDefined();
    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Externe bronnen niet opgeslagen');
  });

  it('saves turning it back on', async () => {
    backend.data.sites[0]!.externalSources = false;

    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="externe-bronnen"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.externalSources).toBe(true);
    expect(wrapper.find('[data-testid="externe-bronnen"]').attributes('checked')).toBeDefined();
    expect(
      wrapper.find('nldd-notification[text="Externe bronnen opgeslagen"]').attributes(
        'supporting-text',
      ),
    ).toBe('Externe bronnen staan aan.');
  });

  it('does nothing when the switch reports the value it already has', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="externe-bronnen"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.externalSources).toBe(true);
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });
});

describe('TabAccess: shielding from other sites', () => {
  it('is on by default, and names both what it costs and what it keeps', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const field = wrapper.find('[data-testid="afscherming"]');
    expect(field.element.tagName.toLowerCase()).toBe('nldd-switch-field');
    expect(field.attributes('label')).toBe('Afschermen van andere sites');
    expect(field.attributes('checked')).toBeDefined();

    const section = wrapper.find('section[aria-labelledby="kop-afscherming"]');
    expect(section.text()).toContain('Staat aan, tenzij je het uitzet');
    // The price has to be in the words a publisher recognises, not only in
    // the API names: this is the switch that breaks a working site.
    expect(section.text()).toContain('onthouden voorkeur');
    expect(section.text()).toContain('localStorage');
    expect(section.text()).toContain('cookie');
    // And what keeps working, or turning it off looks like the only way to
    // get a page with styling and scripts at all.
    expect(section.text()).toContain('Eigen stijlen, gewone scripts en afbeeldingen laden gewoon');
    // Fonts and a script reading the site's own files do not survive the
    // opaque origin. Claiming otherwise sends a publisher hunting elsewhere.
    expect(section.text()).toContain('Weblettertypen laden niet');
    expect(section.text()).toContain('bestand van je site ophaalt');
    // Module scripts are what an Astro or Vite build emits, and the one
    // failure a publisher cannot map onto "scripts load as usual".
    expect(section.text()).toContain('<script type="module">');
    expect(section.text()).toContain('Astro, Vite');
  });

  it('is off when the site has turned it off', async () => {
    backend.data.sites[0]!.sandbox = false;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="afscherming"]').attributes('checked')).toBeUndefined();
  });

  it('saves turning it off', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="afscherming"]').element, 'change', {
      checked: false,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.sandbox).toBe(false);
    expect(wrapper.find('[data-testid="afscherming"]').attributes('checked')).toBeUndefined();
    expect(wrapper.find('nldd-notification[text="Afscherming opgeslagen"]').exists()).toBe(true);
  });

  it('saves turning it back on', async () => {
    backend.data.sites[0]!.sandbox = false;

    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="afscherming"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.sandbox).toBe(true);
    expect(wrapper.find('[data-testid="afscherming"]').attributes('checked')).toBeDefined();
  });

  it('rolls back the switch and reports it when saving fails', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    fireDetailEvent(wrapper.find('[data-testid="afscherming"]').element, 'change', {
      checked: false,
    });
    await untilIdle();

    expect(wrapper.find('[data-testid="afscherming"]').attributes('checked')).toBeDefined();
    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Afscherming niet opgeslagen');
  });

  it('does nothing when the switch reports the value it already has', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="afscherming"]').element, 'change', {
      checked: true,
    });
    await untilIdle();

    expect(backend.data.sites[0]!.sandbox).toBe(true);
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });
});

describe('TabAccess: invitees', () => {
  it('sits as a table on the page itself, with a row menu and the form below', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const table = wrapper.find('[data-testid="genodigden-lijst"]');
    expect(table.element.tagName.toLowerCase()).toBe('nldd-table');
    // No sheet left on this tab: the lists are what the tab is about.
    expect(wrapper.find('nldd-sheet').exists()).toBe(false);

    const header = table.find('nldd-table-row[slot="header"]');
    const columns = header.findAll('nldd-text-cell').map((c) => c.attributes('text'));
    expect(columns).toContain('E-mailadres');
    expect(columns).toContain('Toegevoegd');
    // A columnheader without a name is an axe violation, so the menu column is
    // named for a screen reader and hidden for everyone else.
    expect(header.find('.alleen-schermlezer').text()).toBe('Acties');

    const row = wrapper.find('[data-testid="genodigde-reviewer@voorbeeld.nl"]');
    expect(row.exists()).toBe(true);
    expect(row.find('[data-testid="acties-reviewer@voorbeeld.nl"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="genodigde-formulier"]').exists()).toBe(true);
  });

  it('adds an invitee by email address and shows the row right away', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="genodigde-email"]').element, 'input', {
      value: 'nieuw@voorbeeld.nl',
    });
    await wrapper.find('[data-testid="genodigde-formulier"]').trigger('submit');

    // Optimistic: the row is there before the server has answered.
    expect(wrapper.find('[data-testid="genodigde-nieuw@voorbeeld.nl"]').exists()).toBe(true);

    await untilIdle();
    expect(backend.data.invitees.map((g) => g.identifier)).toContain('nieuw@voorbeeld.nl');
    expect(wrapper.find('[data-testid="genodigde-nieuw@voorbeeld.nl"]').exists()).toBe(true);
  });

  it('removes an invitee it just added, by the id the server gave back', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="genodigde-email"]').element, 'input', {
      value: 'nieuw@voorbeeld.nl',
    });
    await wrapper.find('[data-testid="genodigde-formulier"]').trigger('submit');
    await untilIdle();

    fireDetailEvent(
      wrapper.find('[data-testid="genodigde-verwijderen-nieuw@voorbeeld.nl"]').element,
      'select',
      {},
    );
    await untilIdle();

    expect(backend.data.invitees.map((g) => g.identifier)).not.toContain('nieuw@voorbeeld.nl');
    expect(wrapper.find('[data-testid="genodigde-nieuw@voorbeeld.nl"]').exists()).toBe(false);
  });

  it('rolls back the row and shows the backend error on a duplicate', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="genodigde-email"]').element, 'input', {
      value: 'reviewer@voorbeeld.nl',
    });
    await wrapper.find('[data-testid="genodigde-formulier"]').trigger('submit');
    await untilIdle();

    expect(wrapper.find('nldd-validation-item#genodigde-server').text()).toContain(
      'staat al op de genodigdenlijst',
    );
    const field = wrapper.find('[data-testid="genodigde-email"]');
    expect(field.attributes('invalid')).toBeDefined();
    expect(field.attributes('unmet')).toBe('genodigde-server');
    // The entered value comes back so the user can correct it.
    expect(field.attributes('value')).toBe('reviewer@voorbeeld.nl');
    expect(backend.data.invitees).toHaveLength(1);
    expect(wrapper.findAll('[data-testid="genodigde-reviewer@voorbeeld.nl"]')).toHaveLength(1);
  });

  it('validates that an email address is filled in', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="genodigde-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.invitees).toHaveLength(1);
    // nldd-form-field links label and validation list to direct children only;
    // an intervening container breaks the accessible name and the tie between
    // the requirements and the field.
    const field = wrapper.find('[data-testid="genodigde-email"]').element;
    expect(field.parentElement?.tagName.toLowerCase()).toBe('nldd-form-field');
    expect(field.getAttribute('required')).not.toBeNull();
    expect(field.getAttribute('invalid')).not.toBeNull();
    expect(field.getAttribute('unmet')).toBeNull();
    const requirement = wrapper.find('#genodigde-email-vereist').element;
    expect(requirement.tagName.toLowerCase()).toBe('nldd-validation-item');
    expect(requirement.hasAttribute('required')).toBe(true);
    expect(requirement.textContent?.trim()).toBe('Een e-mailadres');
    expect(requirement.parentElement?.tagName.toLowerCase()).toBe('nldd-validation-list');
    expect(requirement.parentElement?.parentElement).toBe(field.parentElement);
  });

  it('removes an invitee from the row menu', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const action = wrapper.find('[data-testid="genodigde-verwijderen-reviewer@voorbeeld.nl"]');
    expect(action.attributes('destructive')).toBeDefined();
    action.element.dispatchEvent(new CustomEvent('select'));
    await untilIdle();

    expect(wrapper.find('[data-testid="genodigde-reviewer@voorbeeld.nl"]').exists()).toBe(false);
    expect(backend.data.invitees).toHaveLength(0);
  });

  it('rolls back a fresh invitee entirely when adding it fails outright', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    fireDetailEvent(wrapper.find('[data-testid="genodigde-email"]').element, 'input', {
      value: 'nieuw@voorbeeld.nl',
    });
    await wrapper.find('[data-testid="genodigde-formulier"]').trigger('submit');
    // Optimistic row shown before the server answers, same as on the happy path.
    expect(wrapper.find('[data-testid="genodigde-nieuw@voorbeeld.nl"]').exists()).toBe(true);
    await untilIdle();

    // Unlike the duplicate case, this row was never on the server's list, so
    // the whole provisional row comes back out, not just its optimistic state.
    expect(wrapper.find('[data-testid="genodigde-nieuw@voorbeeld.nl"]').exists()).toBe(false);
    expect(backend.data.invitees).toHaveLength(1);
    expect(wrapper.find('nldd-validation-item#genodigde-server').text()).toBe('Serverfout');
  });

  it('rolls back a failed removal and reports it', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    wrapper
      .find('[data-testid="genodigde-verwijderen-reviewer@voorbeeld.nl"]')
      .element.dispatchEvent(new CustomEvent('select'));
    await untilIdle();

    expect(wrapper.find('[data-testid="genodigde-reviewer@voorbeeld.nl"]').exists()).toBe(true);
    expect(wrapper.find('nldd-notification[variant="critical"]').exists()).toBe(true);
  });
});

describe('TabAccess: secret links', () => {
  it('sits as a table on the page itself, with the useful columns', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const table = wrapper.find('[data-testid="sleutels-lijst"]');
    expect(table.element.tagName.toLowerCase()).toBe('nldd-table');
    const header = table.find('nldd-table-row[slot="header"]');
    const columns = header.findAll('nldd-text-cell').map((c) => c.attributes('text'));
    expect(columns).toContain('Label');
    expect(columns).toContain('Aangemaakt');
    expect(columns).toContain('Vervalt');
    expect(columns).toContain('Status');
    expect(header.find('.alleen-schermlezer').text()).toBe('Acties');

    const row = wrapper.find('[data-testid="sleutel-sel-abc123"]');
    expect(row.html()).toContain('Demo voor stakeholders');
    expect(row.find('nldd-tag').attributes('text')).toBe('Actief');
    expect(row.find('[data-testid="acties-Demo voor stakeholders"]').exists()).toBe(true);
  });

  it('creates a key from the form and shows the full link exactly once', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="sleutel-label"]').element, 'input', {
      value: 'Demo klanten',
    });
    await wrapper.find('[data-testid="sleutel-dagen"]').setValue('30');
    await wrapper.find('[data-testid="sleutel-formulier"]').trigger('submit');
    await untilIdle();

    const secret = wrapper.find('[data-testid="nieuwe-sleutel-link"]');
    expect(secret.exists()).toBe(true);
    // The secret link lives on the content host, not on the admin origin.
    expect(secret.text()).toContain('https://sites.plak.test/nldd/website/?key=');
    expect(secret.text()).not.toContain(window.location.origin);
    expect(backend.data.keys).toHaveLength(2);
    const newKey = backend.data.keys[1]!;
    expect(newKey.label).toBe('Demo klanten');
    expect(newKey.expiresAt).not.toBeNull();
    // And the new row is in the table straight away.
    expect(wrapper.find(`[data-testid="sleutel-${newKey.selector}"]`).exists()).toBe(true);

    // Shown once: after reloading the tab the link is gone and nothing in the
    // table holds the secret value.
    const again = makeWrapper();
    await untilIdle();
    expect(again.find('[data-testid="nieuwe-sleutel-link"]').exists()).toBe(false);
    expect(again.html()).not.toContain('?key=');
  });

  it('offers the link also without the code, with the code separate', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="sleutel-label"]').element, 'input', {
      value: 'Demo klanten',
    });
    await wrapper.find('[data-testid="sleutel-formulier"]').trigger('submit');
    await untilIdle();

    const selector = backend.data.keys[1]!.selector;
    const bare = wrapper.find('[data-testid="nieuwe-sleutel-link-zonder-code"]');
    expect(bare.text()).toBe(`https://sites.plak.test/nldd/website/?key=${selector}`);
    const code = wrapper.find('[data-testid="nieuwe-sleutel-code"]').text();
    expect(code).not.toBe('');
    expect(bare.text()).not.toContain(code);
    expect(wrapper.find('[data-testid="nieuwe-sleutel-link"]').text()).toContain(code);
  });

  it('shows the created link as success, with a button to copy and open', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="sleutel-label"]').element, 'input', {
      value: 'Demo klanten',
    });
    await wrapper.find('[data-testid="sleutel-formulier"]').trigger('submit');
    await untilIdle();

    const banner = wrapper.find('[data-testid="nieuwe-sleutel"]');
    expect(banner.attributes('variant')).toBe('success');
    expect(banner.attributes('supporting-text')).toContain('iedereen met deze link');
    expect(wrapper.find('[data-testid="nieuwe-sleutel-kopieren"]').attributes('text')).toBe(
      'Kopieer link',
    );
    expect(wrapper.find('[data-testid="nieuwe-sleutel-openen"]').attributes('target')).toBe(
      '_blank',
    );
  });

  it('no longer offers "Nooit" and creates a key with the default term of 90 days without touching the expiry dropdown', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="sleutel-dagen"] option[value=""]').exists()).toBe(false);

    fireDetailEvent(wrapper.find('[data-testid="sleutel-label"]').element, 'input', {
      value: 'Demo standaardtermijn',
    });
    await wrapper.find('[data-testid="sleutel-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.keys).toHaveLength(2);
    const newKey = backend.data.keys[1]!;
    const expected = new Date(Date.now() + 90 * 24 * 60 * 60 * 1000).getTime();
    expect(Math.abs(new Date(newKey.expiresAt!).getTime() - expected)).toBeLessThan(60_000);
  });

  it('marks the label as optional, not as required', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const field = wrapper.find('[data-testid="sleutel-label"]');
    expect(field.attributes('required')).toBeUndefined();
    expect(wrapper.find('nldd-form-field[label="Label"]').attributes('optional')).toBeDefined();
  });

  it('creates a key without a label with a name based on the date', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="sleutel-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.keys).toHaveLength(2);
    const newKey = backend.data.keys[1]!;
    expect(newKey.label).toMatch(/^Link van \d{1,2} /);
    // The table shows the generated name.
    expect(wrapper.find(`[data-testid="sleutel-${newKey.selector}"]`).html()).toContain(
      newKey.label,
    );
  });

  it('shows the server error on the label field when creating a key fails', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    fireDetailEvent(wrapper.find('[data-testid="sleutel-label"]').element, 'input', {
      value: 'Demo klanten',
    });
    await wrapper.find('[data-testid="sleutel-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.keys).toHaveLength(1);
    expect(wrapper.find('[data-testid="nieuwe-sleutel"]').exists()).toBe(false);
    const field = wrapper.find('[data-testid="sleutel-label"]');
    expect(field.attributes('invalid')).toBeDefined();
    expect(field.attributes('unmet')).toBe('sleutel-server');
    expect(wrapper.find('nldd-validation-item#sleutel-server').text()).toBe('Serverfout');
  });

  it('leaves an untouched key alone when revoking another one', async () => {
    backend.data.keys.push({
      siteSlug: 'website',
      groupSlug: 'nldd',
      label: 'Tweede link',
      selector: 'sel-def456',
      status: 'active',
      createdAt: backend.data.keys[0]!.createdAt,
      expiresAt: null,
    });
    const wrapper = makeWrapper();
    await untilIdle();

    wrapper
      .find('[data-testid="sleutel-intrekken-sel-abc123"]')
      .element.dispatchEvent(new CustomEvent('select'));
    await untilIdle();

    expect(backend.data.keys[0]!.status).toBe('revoked');
    expect(backend.data.keys[1]!.status).toBe('active');
    expect(
      wrapper.find('[data-testid="sleutel-sel-def456"]').find('nldd-tag').attributes('text'),
    ).toBe('Actief');
  });

  it('revokes a key from the row menu and leaves the row without a menu', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    wrapper
      .find('[data-testid="sleutel-intrekken-sel-abc123"]')
      .element.dispatchEvent(new CustomEvent('select'));
    await untilIdle();

    expect(backend.data.keys[0]!.status).toBe('revoked');
    const row = wrapper.find('[data-testid="sleutel-sel-abc123"]');
    expect(row.exists()).toBe(true);
    expect(row.find('nldd-tag').attributes('text')).toBe('Ingetrokken');
    // Nothing left to do with it, so no menu: the row is history now.
    expect(row.find('[data-testid="sleutel-intrekken-sel-abc123"]').exists()).toBe(false);
    expect(row.find('nldd-icon-button').exists()).toBe(false);
  });

  it('rolls back a failed revocation and reports it', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    wrapper
      .find('[data-testid="sleutel-intrekken-sel-abc123"]')
      .element.dispatchEvent(new CustomEvent('select'));
    await untilIdle();

    expect(wrapper.find('[data-testid="sleutel-sel-abc123"]').find('nldd-tag').attributes('text')).toBe(
      'Actief',
    );
    expect(wrapper.find('nldd-notification[variant="critical"]').exists()).toBe(true);
  });

  it('copies the link and confirms it beside the button', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: write },
      configurable: true,
    });
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="sleutel-label"]').element, 'input', {
      value: 'Demo klanten',
    });
    await wrapper.find('[data-testid="sleutel-formulier"]').trigger('submit');
    await untilIdle();

    await wrapper.find('[data-testid="nieuwe-sleutel-kopieren"]').trigger('click');
    await untilIdle();

    expect(write).toHaveBeenCalledWith(expect.stringContaining('?key='));
    const notice = wrapper.find('[data-testid="nieuwe-sleutel-melding"]');
    expect(notice.text()).toBe('Link gekopieerd.');
    expect(notice.attributes('role')).toBe('status');
  });

  it('falls back to the DOM value when the field fires a plain input event without a detail', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const field = wrapper.find('[data-testid="sleutel-label"]').element as HTMLInputElement & {
      value: string;
    };
    // Not every input source is the nldd wrapper's CustomEvent: a plain
    // native 'input' event carries the value on the target instead.
    field.value = 'Van het element zelf';
    field.dispatchEvent(new Event('input'));
    await wrapper.find('[data-testid="sleutel-formulier"]').trigger('submit');
    await untilIdle();

    expect(backend.data.keys[1]!.label).toBe('Van het element zelf');
  });

  it('falls back to an empty string when neither the detail nor the target carry a value', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const field = wrapper.find('[data-testid="sleutel-label"]').element;
    // Neither a detail nor a DOM value property: inputValue lands on ''.
    field.dispatchEvent(new Event('input'));
    await wrapper.find('[data-testid="sleutel-formulier"]').trigger('submit');
    await untilIdle();

    // Same as submitting without ever touching the field: the server makes up
    // a date-based label because the trimmed value is empty.
    expect(backend.data.keys[1]!.label).toMatch(/^Link van \d{1,2} /);
  });

  it('points to the link itself when the clipboard is denied', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn().mockRejectedValue(new Error('geen toestemming')) },
      configurable: true,
    });
    const wrapper = makeWrapper();
    await untilIdle();

    fireDetailEvent(wrapper.find('[data-testid="sleutel-label"]').element, 'input', {
      value: 'Demo klanten',
    });
    await wrapper.find('[data-testid="sleutel-formulier"]').trigger('submit');
    await untilIdle();
    await wrapper.find('[data-testid="nieuwe-sleutel-kopieren"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="nieuwe-sleutel-melding"]').text()).toContain(
      'Selecteer de link',
    );
  });
});
