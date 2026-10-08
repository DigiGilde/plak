import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
import type { Access, PreviousSlug, Site } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import { fireDetailEvent, problemFetch, serverErrorFetch } from '@/components/site/testHelpers';
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

function makeWrapper(
  props: {
    access?: Access;
    sites?: Site[];
    previousSlugs?: PreviousSlug[];
    redirectDays?: number;
    canDelete?: boolean;
    canRename?: boolean;
    canChangeAddress?: boolean;
    groupName?: string;
    /** In the document, for a test that needs to know where the focus is. */
    attach?: boolean;
  } = {},
) {
  return mount(TabSettings, {
    props: {
      group: 'team-aurora',
      groupName: props.groupName ?? 'Team Aurora',
      access: props.access ?? access,
      sites: props.sites ?? [],
      previousSlugs: props.previousSlugs ?? [],
      contentBase: MOCK_CONTENT_BASE,
      redirectDays: props.redirectDays ?? 30,
      canDelete: props.canDelete ?? false,
      canRename: props.canRename ?? false,
      canChangeAddress: props.canChangeAddress ?? false,
    },
    global: { stubs: { teleport: true } },
    attachTo: props.attach ? document.body : undefined,
  });
}

function fireChange(el: Element, checked: boolean): void {
  el.dispatchEvent(new CustomEvent('change', { detail: { checked } }));
}

describe('group TabSettings', () => {
  it('saves a chosen base through the real api.setGroupDefaultAccess', async () => {
    const wrapper = makeWrapper();

    await wrapper.find('[data-testid="default-access-site_team"]').trigger('change');
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

    await wrapper.find('[data-testid="default-access-public"]').trigger('change');
    await flushPromises();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual(access);
    expect(wrapper.emitted('groupChanged')).toBeUndefined();
  });

  it('turns on the keys exception', async () => {
    const wrapper = makeWrapper();

    fireChange(wrapper.find('[data-testid="default-access-keys"]').element, true);
    await flushPromises();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual({
      base: 'public',
      keys: true,
      invitees: false,
    });
  });

  it('turns on the invitees exception', async () => {
    const wrapper = makeWrapper();

    fireChange(wrapper.find('[data-testid="default-access-invitees"]').element, true);
    await flushPromises();

    expect(backend.data.groups.find((g) => g.slug === 'team-aurora')?.defaultAccess).toEqual({
      base: 'public',
      keys: false,
      invitees: true,
    });
  });

  it('leaves an extra untouched when switched to its own current value', async () => {
    const wrapper = makeWrapper();

    fireChange(wrapper.find('[data-testid="default-access-keys"]').element, false);
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
    await wrapper.find('[data-testid="default-access-site_team"]').trigger('change');
    await flushPromises();

    expect(
      wrapper.find('[data-testid="default-access-public"]').attributes('checked'),
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
    await wrapper.find('[data-testid="default-access-site_team"]').trigger('change');
    await flushPromises();

    expect(
      wrapper.find('nldd-notification[text="Standaardtoegang niet opgeslagen"]').attributes(
        'supporting-text',
      ),
    ).toBe('Opslaan is niet gelukt.');
  });
});

describe('group TabSettings: name', () => {
  function typeName(wrapper: ReturnType<typeof makeWrapper>, value: string): void {
    fireDetailEvent(wrapper.find('[data-testid="group-name"]').element, 'input', { value });
  }

  async function submitName(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
    await wrapper.find('[data-testid="group-name-form"]').trigger('submit');
    await flushPromises();
  }

  function serverText(wrapper: ReturnType<typeof makeWrapper>): string {
    return wrapper.find('nldd-validation-item#group-settings-name-server').text();
  }

  /** The line that says what became of a save. */
  function statusLine(wrapper: ReturnType<typeof makeWrapper>) {
    return wrapper.find('[data-testid="group-name-notice"]');
  }

  it('puts the section first, under its own heading', () => {
    const wrapper = makeWrapper({ canRename: true });

    expect(wrapper.findAll('h2')[0]!.text()).toBe('Naam van de groep');
    expect(wrapper.find('section[aria-labelledby="heading-group-name"] h2').attributes('id')).toBe(
      'heading-group-name',
    );
  });

  it('gives a group admin a field with the current name and one save button', () => {
    const wrapper = makeWrapper({ canRename: true });

    const field = wrapper.find('[data-testid="group-name"]');
    expect(field.element.tagName.toLowerCase()).toBe('nldd-text-field');
    expect(field.attributes('name')).toBe('group-name');
    expect(field.attributes('value')).toBe('Team Aurora');
    expect(field.attributes('required')).toBeDefined();
    expect(field.attributes('autocomplete')).toBe('off');
    expect(field.attributes('invalid')).toBeUndefined();
    // nldd-form-field links its label and validation list to direct children.
    expect(field.element.parentElement?.tagName.toLowerCase()).toBe('nldd-form-field');
    expect(field.element.parentElement?.getAttribute('label')).toBe('Naam');

    const buttons = wrapper.findAll('nldd-form nldd-button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]!.attributes('text')).toBe('Bewaar naam');
    expect(buttons[0]!.attributes('type')).toBe('submit');
    expect(buttons[0]!.attributes('variant')).toBe('primary');
    expect(buttons[0]!.attributes('disabled')).toBeUndefined();
    expect(wrapper.find('nldd-form-section').exists()).toBe(false);
    expect(wrapper.find('[data-testid="group-name-text"]').exists()).toBe(false);
  });

  it('has the status line in the page from the first render, empty, for a screen reader to watch', () => {
    const wrapper = makeWrapper({ canRename: true });

    const line = statusLine(wrapper);
    expect(line.exists()).toBe(true);
    expect(line.attributes('role')).toBe('status');
    expect(line.text()).toBe('');
  });

  it('judges the empty field with a requirement and keeps the server verdict empty', () => {
    const wrapper = makeWrapper({ canRename: true });

    const required = wrapper.find('nldd-validation-item#group-settings-name-required');
    expect(required.text()).toBe('Een naam is nodig');
    expect(required.attributes('required')).toBeDefined();
    expect(serverText(wrapper)).toBe('');
  });

  it('states the limit before anything is typed, as a hint that judges nothing itself', () => {
    const wrapper = makeWrapper({ canRename: true });

    const hint = wrapper.find('nldd-validation-item#group-settings-name-length');
    expect(hint.text()).toBe('Een naam is hoogstens 200 tekens lang');
    expect(hint.attributes('hint')).toBeDefined();
    // The server counts, so the field does not hold the name to a rule of its own.
    expect(hint.attributes('maxlength')).toBeUndefined();
    expect(hint.attributes('match')).toBeUndefined();
    expect(wrapper.find('[data-testid="group-name"]').attributes('maxlength')).toBeUndefined();
  });

  it('shows the name as text, and who may change it, to everyone else', () => {
    const wrapper = makeWrapper({ canRename: false });

    expect(wrapper.find('[data-testid="group-name-form"]').exists()).toBe(false);
    expect(wrapper.find('nldd-text-field').exists()).toBe(false);
    const name = wrapper.find('[data-testid="group-name-text"]');
    expect(name.text()).toBe('Team Aurora');
    expect(name.element.closest('nldd-rich-text')).not.toBeNull();
    expect(wrapper.find('section[aria-labelledby="heading-group-name"]').text()).toContain(
      'Alleen een beheerder van de groep kan de naam wijzigen.',
    );
  });

  it('saves the typed name through the real api.setGroupName', async () => {
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    expect(backend.data.groups[0]!.name).toBe('Team Zonsopgang');
    expect(wrapper.emitted('groupChanged')).toHaveLength(1);
    expect(wrapper.emitted('groupChanged')![0]![0]).toMatchObject({
      slug: 'team-aurora',
      name: 'Team Zonsopgang',
    });
    // A line in the page, not a notification that comes and goes.
    expect(statusLine(wrapper).text()).toBe('Naam opgeslagen. De groep heet nu Team Zonsopgang.');
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('shows the name the server saved, not the one that was typed', async () => {
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, '  Team Zonsopgang  ');
    await submitName(wrapper);

    expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe('Team Zonsopgang');
    expect(statusLine(wrapper).text()).toBe('Naam opgeslagen. De groep heet nu Team Zonsopgang.');
  });

  it('takes the value of a native input event too', async () => {
    const wrapper = makeWrapper({ canRename: true });

    const field = wrapper.find('[data-testid="group-name"]').element as HTMLElement & {
      value?: string;
    };
    field.value = 'Via native input';
    field.dispatchEvent(new Event('input'));
    await submitName(wrapper);

    expect(backend.data.groups[0]!.name).toBe('Via native input');
  });

  it('sends nothing and says that nothing changed when the name is the current one', async () => {
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, '  Team Aurora ');
    await submitName(wrapper);

    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.emitted('groupChanged')).toBeUndefined();
    // The button never does nothing, though there is nothing to save.
    expect(statusLine(wrapper).text()).toBe('De naam is niet gewijzigd.');
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('says it again when the button is pressed again with nothing changed', async () => {
    const wrapper = makeWrapper({ canRename: true });
    await submitName(wrapper);
    const line = statusLine(wrapper).element;
    expect(line.textContent?.trim()).toBe('De naam is niet gewijzigd.');

    // A live region only speaks when its words change, so they go and come back.
    const changes: MutationRecord[] = [];
    const observer = new MutationObserver((records) => changes.push(...records));
    observer.observe(line, { childList: true, characterData: true, subtree: true });
    await submitName(wrapper);
    observer.disconnect();

    expect(line.textContent?.trim()).toBe('De naam is niet gewijzigd.');
    expect(changes.some((change) => change.removedNodes.length > 0)).toBe(true);
    expect(changes.some((change) => change.addedNodes.length > 0)).toBe(true);
  });

  it('takes the line back when the name is edited', async () => {
    const wrapper = makeWrapper({ canRename: true });
    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);
    expect(statusLine(wrapper).text()).not.toBe('');

    typeName(wrapper, 'Team Zon');
    await flushPromises();

    expect(statusLine(wrapper).text()).toBe('');
  });

  it('leaves the line empty while the next save is on its way', async () => {
    const wrapper = makeWrapper({ canRename: true });
    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);
    expect(statusLine(wrapper).text()).not.toBe('');

    vi.stubGlobal('fetch', () => new Promise<Response>(() => {}));
    typeName(wrapper, 'Team Horizon');
    await wrapper.find('[data-testid="group-name-form"]').trigger('submit');

    expect(statusLine(wrapper).text()).toBe('');
  });

  it('sends the name once while the first save is still on its way', async () => {
    let finish: (response: Response) => void = () => {};
    const spy = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    vi.stubGlobal('fetch', spy);
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, 'Team Zonsopgang');
    await wrapper.find('[data-testid="group-name-form"]').trigger('submit');
    await wrapper.find('[data-testid="group-name-form"]').trigger('submit');

    expect(spy).toHaveBeenCalledTimes(1);
    expect(wrapper.find('[data-testid="group-name-save"]').attributes('loading')).toBeDefined();

    finish(
      new Response(
        JSON.stringify({ slug: 'team-aurora', name: 'Team Zonsopgang', defaultAccess: access }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );
    await flushPromises();

    expect(wrapper.find('[data-testid="group-name-save"]').attributes('loading')).toBeUndefined();
    expect(statusLine(wrapper).text()).toBe('Naam opgeslagen. De groep heet nu Team Zonsopgang.');
  });

  describe.each([
    ['   ', 'FIELD_EMPTY', 'Een naam is nodig'],
    ['a'.repeat(201), 'FIELD_TOO_LONG', 'Een naam is hoogstens 200 tekens lang'],
    [
      'Team\tAurora',
      'FIELD_CONTROL_CHARACTERS',
      'Een naam bevat geen onzichtbare tekens of regeleinden, die vaak meekomen met gekopieerde tekst',
    ],
  ])('when the server refuses %j with %s', (typed, _code, text) => {
    it('says what is wrong at the field, in the words of the interface', async () => {
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, typed);
      await submitName(wrapper);

      const field = wrapper.find('[data-testid="group-name"]');
      expect(field.attributes('invalid')).toBeDefined();
      expect(field.attributes('unmet')).toBe('group-settings-name-server');
      expect(serverText(wrapper)).toBe(text);
      // The server's own sentence is not what the member reads.
      expect(wrapper.html()).not.toContain('mag niet leeg zijn');
      expect(wrapper.html()).not.toContain('mag hoogstens');
      expect(wrapper.html()).not.toContain('stuur- of opmaaktekens');
      expect(wrapper.find('nldd-notification').exists()).toBe(false);
      expect(wrapper.emitted('groupChanged')).toBeUndefined();
      expect(backend.data.groups[0]!.name).toBe('Team Aurora');
    });

    it('keeps what was typed and moves the focus to the field', async () => {
      const focus = vi.spyOn(HTMLElement.prototype, 'focus');
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, typed);
      await submitName(wrapper);

      expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe(typed);
      expect(focus).toHaveBeenCalledTimes(1);
      expect(focus.mock.contexts[0]).toBe(wrapper.find('[data-testid="group-name"]').element);
      focus.mockRestore();
    });

    it('says the verdict aloud too, which is all there is to hear when Enter was pressed in the field', async () => {
      const wrapper = makeWrapper({ canRename: true, attach: true });
      const field = wrapper.find('[data-testid="group-name"]').element as HTMLElement;
      // Pressing Enter submits from the field, which has the focus before the answer comes.
      field.setAttribute('tabindex', '0');
      field.focus();
      expect(document.activeElement).toBe(field);

      typeName(wrapper, typed);
      await submitName(wrapper);

      // Moving the focus to where it already is says nothing; the alert does.
      expect(document.activeElement).toBe(field);
      const alert = wrapper.find('[role="alert"]');
      expect(alert.text()).toBe(text);
      // The same words are on screen at the field already.
      expect(alert.classes()).toContain('visually-hidden');
    });
  });

  it('says nothing aloud while nothing is refused', async () => {
    const wrapper = makeWrapper({ canRename: true });
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);

    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });

  it('says it again when the same refusal comes back, and takes the alert back on an edit', async () => {
    const wrapper = makeWrapper({ canRename: true });
    typeName(wrapper, '   ');
    await submitName(wrapper);
    const first = wrapper.find('[role="alert"]').element;

    // A live region only speaks when something new appears in it.
    await submitName(wrapper);
    const second = wrapper.find('[role="alert"]').element;
    expect(second).not.toBe(first);
    expect(second.textContent?.trim()).toBe('Een naam is nodig');

    typeName(wrapper, 'Team Z');
    await flushPromises();
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });

  it('leaves the alert to a refusal about the text: another failure is a notification', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    expect(wrapper.find('nldd-notification[text="Naam niet opgeslagen"]').exists()).toBe(true);
  });

  it('shows the SPA text for the code the server named, whatever the server wrote', async () => {
    vi.stubGlobal(
      'fetch',
      problemFetch(422, {
        title: 'Unprocessable',
        detail: 'Field "name" must not be empty.',
        code: 'FIELD_EMPTY',
      }),
    );
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, 'Nieuw');
    await submitName(wrapper);

    expect(serverText(wrapper)).toBe('Een naam is nodig');
  });

  it('takes the verdict back as soon as the name is edited', async () => {
    const wrapper = makeWrapper({ canRename: true });
    typeName(wrapper, '   ');
    await submitName(wrapper);
    expect(wrapper.find('[data-testid="group-name"]').attributes('invalid')).toBeDefined();

    typeName(wrapper, 'Team Z');
    await flushPromises();

    const field = wrapper.find('[data-testid="group-name"]');
    expect(field.attributes('invalid')).toBeUndefined();
    expect(field.attributes('unmet')).toBeUndefined();
    expect(serverText(wrapper)).toBe('');
  });

  it('lets a corrected name through after a refusal', async () => {
    const wrapper = makeWrapper({ canRename: true });
    typeName(wrapper, '   ');
    await submitName(wrapper);

    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    expect(backend.data.groups[0]!.name).toBe('Team Zonsopgang');
    expect(wrapper.find('[data-testid="group-name"]').attributes('invalid')).toBeUndefined();
    expect(statusLine(wrapper).text()).toBe('Naam opgeslagen. De groep heet nu Team Zonsopgang.');
  });

  it('leaves the line empty when the save is refused, the verdict being elsewhere', async () => {
    const wrapper = makeWrapper({ canRename: true });
    typeName(wrapper, '   ');
    await submitName(wrapper);

    expect(statusLine(wrapper).text()).toBe('');

    vi.stubGlobal('fetch', serverErrorFetch());
    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    expect(statusLine(wrapper).text()).toBe('');
  });

  it('reports a refusal that is not about the text as a critical notice, with the field left clean', async () => {
    vi.stubGlobal(
      'fetch',
      problemFetch(429, {
        title: 'Te veel verzoeken',
        detail: 'Probeer het over een minuut opnieuw.',
        code: 'RATE_LIMITED',
      }),
    );
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    const notice = wrapper.find('nldd-notification[text="Naam niet opgeslagen"]');
    expect(notice.attributes('variant')).toBe('critical');
    expect(notice.attributes('supporting-text')).toBe('Probeer het over een minuut opnieuw.');
    expect(wrapper.find('[data-testid="group-name"]').attributes('invalid')).toBeUndefined();
    expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe('Team Zonsopgang');
    expect(wrapper.emitted('groupChanged')).toBeUndefined();
  });

  it('reports the title of a problem without a detail', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    expect(
      wrapper.find('nldd-notification[text="Naam niet opgeslagen"]').attributes('supporting-text'),
    ).toBe('Serverfout');
  });

  it('reports a refusal by the role as a critical notice', async () => {
    // The role was taken away in the meantime: the server has the last word.
    backend.data.loggedInMemberId = 'lid-3';
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    const notice = wrapper.find('nldd-notification[text="Naam niet opgeslagen"]');
    expect(notice.attributes('variant')).toBe('critical');
    expect(notice.attributes('supporting-text')).toBe(
      'Hiervoor heb je minimaal de rol beheerder nodig.',
    );
  });

  it('reports a generic failure when saving throws something other than an ApiError', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    expect(
      wrapper.find('nldd-notification[text="Naam niet opgeslagen"]').attributes('supporting-text'),
    ).toBe('Opslaan is niet gelukt.');
  });

  it('can save again after a failure', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());
    const wrapper = makeWrapper({ canRename: true });
    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    vi.stubGlobal('fetch', backend.fetch);
    await submitName(wrapper);

    expect(backend.data.groups[0]!.name).toBe('Team Zonsopgang');
  });
});

describe('group TabSettings: address', () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(new Date('2026-10-08T10:00:00Z'));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function typeAddress(wrapper: ReturnType<typeof makeWrapper>, value: string): void {
    fireDetailEvent(wrapper.find('[data-testid="group-address"]').element, 'input', { value });
  }

  it('comes second, between the name and the access new sites start with', () => {
    const wrapper = makeWrapper({ canRename: true, canChangeAddress: true });

    expect(wrapper.findAll('h2').map((h) => h.text())).toEqual([
      'Naam van de groep',
      'Adres van de groep',
      'Standaardtoegang voor nieuwe sites',
    ]);
  });

  it('shows the form to a group admin and the sites it has as an example', () => {
    const wrapper = makeWrapper({
      canChangeAddress: true,
      sites: backend.data.sites,
    });

    expect(wrapper.find('[data-testid="group-address-form"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="group-address-current"]').text()).toContain(
      'https://sites.plak.test/team-aurora/website/',
    );
  });

  it('says who may change it to everyone else, and still lists the old addresses', () => {
    const wrapper = makeWrapper({
      canChangeAddress: false,
      previousSlugs: [{ slug: 'team-oud', redirectsUntil: '2026-11-06T23:00:00Z' }],
    });

    expect(wrapper.find('[data-testid="group-address-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="group-address-readonly"]').text()).toBe(
      'Alleen een beheerder van de groep kan het adres wijzigen.',
    );
    expect(wrapper.find('[data-testid="group-address-previous"]').text()).toContain(
      'team-oud stuurt door tot en met 6 november 2026.',
    );
  });

  it('warns about the address of a public site, and about nothing else', () => {
    const open = { ...backend.data.sites[0]!, access: { base: 'public', keys: false, invitees: false } } as Site;
    const closed = { ...open, slug: 'intern', access: { base: 'site_team', keys: true, invitees: true } } as Site;

    const withPublic = makeWrapper({ canChangeAddress: true, sites: [closed, open] });
    const withoutPublic = makeWrapper({ canChangeAddress: true, sites: [closed] });

    expect(withPublic.find('[data-testid="group-address-public"]').exists()).toBe(true);
    expect(withoutPublic.find('[data-testid="group-address-public"]').exists()).toBe(false);
  });

  it('counts the days the page is told to count', () => {
    const wrapper = makeWrapper({ canChangeAddress: true, redirectDays: 14 });

    expect(wrapper.find('[data-testid="group-address-consequences"]').text()).toContain(
      'Tot en met 22 oktober 2026 kun je het oude adres terugzetten.',
    );
  });

  it('changes the address of the group and hands the group to the page', async () => {
    const wrapper = makeWrapper({ canChangeAddress: true, sites: backend.data.sites });

    typeAddress(wrapper, 'aurora');
    await wrapper.find('[data-testid="group-address-form"]').trigger('submit');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await flushPromises();

    expect(backend.data.groups[0]!.slug).toBe('aurora');
    expect(wrapper.emitted('renamed')).toHaveLength(1);
    expect(wrapper.emitted('renamed')![0]![0]).toMatchObject({
      slug: 'aurora',
      previousSlugs: [{ slug: 'team-aurora' }],
    });
    // The page handles the new group: the tab says nothing of it as a change of the access or the name.
    expect(wrapper.emitted('groupChanged')).toBeUndefined();
  });
});

describe('group TabSettings: danger zone', () => {
  function site(slug: string, title = slug): Site {
    return {
      groupSlug: 'team-aurora',
      slug,
      title,
      access: { base: 'site_team', keys: false, invitees: false },
    } as Site;
  }

  function typeSlug(wrapper: ReturnType<typeof makeWrapper>, value = 'team-aurora'): void {
    fireDetailEvent(wrapper.find('[data-testid="confirm-phrase"]').element, 'input', { value });
  }

  async function confirmDeletion(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
    await wrapper.find('[data-testid="delete-group"]').trigger('click');
    typeSlug(wrapper);
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await flushPromises();
  }

  it('shows nothing of it to someone who may not delete the group', () => {
    const wrapper = makeWrapper({ canDelete: false });

    expect(wrapper.find('nldd-box[background="critical"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="delete-group"]').exists()).toBe(false);
    expect(wrapper.findComponent(ConfirmModal).exists()).toBe(false);
  });

  it('offers the button while the group still has sites', () => {
    const wrapper = makeWrapper({ canDelete: true, sites: [site('website')] });

    expect(wrapper.find('[data-testid="delete-group"]').exists()).toBe(true);
  });

  it('names the sites that go along in the dialog', async () => {
    const wrapper = makeWrapper({ canDelete: true, sites: [site('website', 'Website')] });

    await wrapper.find('[data-testid="delete-group"]').trigger('click');

    expect(wrapper.findComponent(ConfirmModal).props('text')).toContain('deze sites');
    const rows = wrapper.findAll('[data-testid="group-sites-list"] nldd-text-cell');
    expect(rows).toHaveLength(1);
    expect(rows[0]!.attributes('text')).toBe('Website');
    expect(rows[0]!.attributes('supporting-text')).toBe('team-aurora/website');
    expect(wrapper.find('[data-testid="group-sites-rest"]').exists()).toBe(false);
  });

  it('names five sites and counts the rest', async () => {
    const sites = ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((slug) => site(slug));
    const wrapper = makeWrapper({ canDelete: true, sites });

    await wrapper.find('[data-testid="delete-group"]').trigger('click');

    expect(wrapper.findAll('[data-testid="group-sites-list"] nldd-list-item')).toHaveLength(6);
    expect(
      wrapper.find('[data-testid="group-sites-rest"] nldd-text-cell').attributes('text'),
    ).toBe('En nog 2 sites');
  });

  it('says "1 site" when a single one is left unnamed', async () => {
    const sites = ['a', 'b', 'c', 'd', 'e', 'f'].map((slug) => site(slug));
    const wrapper = makeWrapper({ canDelete: true, sites });

    await wrapper.find('[data-testid="delete-group"]').trigger('click');

    expect(
      wrapper.find('[data-testid="group-sites-rest"] nldd-text-cell').attributes('text'),
    ).toBe('En nog 1 site');
  });

  it('lists nothing for an empty group and says only the group goes', async () => {
    const wrapper = makeWrapper({ canDelete: true });

    await wrapper.find('[data-testid="delete-group"]').trigger('click');

    expect(wrapper.find('[data-testid="group-sites-list"]').exists()).toBe(false);
    expect(wrapper.findComponent(ConfirmModal).props('text')).not.toContain('deze sites');
  });

  it('deletes the group with its sites only after its slug is typed', async () => {
    const wrapper = makeWrapper({ canDelete: true, sites: [site('website')] });

    await wrapper.find('[data-testid="delete-group"]').trigger('click');
    expect(wrapper.findComponent(ConfirmModal).props('confirmPhrase')).toBe('team-aurora');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await flushPromises();
    expect(backend.data.groups.map((g) => g.slug)).toContain('team-aurora');
    expect(wrapper.emitted('removed')).toBeUndefined();

    typeSlug(wrapper);
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await flushPromises();

    expect(backend.data.groups.map((g) => g.slug)).not.toContain('team-aurora');
    expect(backend.data.sites.filter((s) => s.groupSlug === 'team-aurora')).toHaveLength(0);
    expect(wrapper.emitted('removed')).toHaveLength(1);
    expect(wrapper.findComponent(ConfirmModal).props('open')).toBe(false);
  });

  it('Behoud groep closes the dialog without deleting', async () => {
    const wrapper = makeWrapper({ canDelete: true });

    await wrapper.find('[data-testid="delete-group"]').trigger('click');
    expect(wrapper.find('[data-testid="confirm-cancel"]').attributes('text')).toBe(
      'Behoud groep',
    );
    await wrapper.find('[data-testid="confirm-cancel"]').trigger('click');
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
    expect(wrapper.find('[data-testid="confirm-continue"]').attributes('loading')).toBeUndefined();
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
