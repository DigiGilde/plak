import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, type MockBackend } from '@/api/mock';
import ConfirmModal from '@/components/ConfirmModal.vue';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import TabSettings from './TabSettings.vue';
import { fireDetailEvent, problemFetch, serverErrorFetch, untilIdle } from './testHelpers';

let backend: MockBackend;

beforeEach(() => {
  // Module-level session cache: every test starts with a fresh /me.
  _resetCurrentMemberCache();
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

/** `attach` puts it in the document, for a test that needs to know where the focus is. */
function makeWrapper(site = 'website', attach = false) {
  return mount(TabSettings, {
    props: { group: 'team-aurora', site },
    global: { stubs: { teleport: true } },
    attachTo: attach ? document.body : undefined,
  });
}

async function loadedWrapper(site = 'website', attach = false) {
  const wrapper = makeWrapper(site, attach);
  await untilIdle();
  return wrapper;
}

function typeTitle(wrapper: ReturnType<typeof makeWrapper>, value: string): void {
  fireDetailEvent(wrapper.find('[data-testid="site-title"]').element, 'input', { value });
}

async function submitTitle(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
  await wrapper.find('[data-testid="site-title-form"]').trigger('submit');
  await untilIdle();
}

function serverText(wrapper: ReturnType<typeof makeWrapper>): string {
  return wrapper.find('nldd-validation-item#site-title-server').text();
}

/** The line that says what became of a save. */
function statusLine(wrapper: ReturnType<typeof makeWrapper>) {
  return wrapper.find('[data-testid="site-title-notice"]');
}

/** Types the site's address, which the delete dialog asks for. */
function typeAddress(wrapper: ReturnType<typeof makeWrapper>, value = 'team-aurora/website'): void {
  fireDetailEvent(wrapper.find('[data-testid="confirm-phrase"]').element, 'input', { value });
}

describe('site TabSettings: states', () => {
  it('shows only the indicator until the data and the role are known', () => {
    // No await: the data is still in flight.
    const wrapper = makeWrapper();

    const indicator = wrapper.find('nldd-activity-indicator');
    expect(indicator.attributes('text')).toBe('Instellingen laden');
    expect(indicator.attributes('complete')).toBeUndefined();
    // Neither the form nor the line that says only an admin may change the
    // title: for an admin both would be wrong for as long as this lasts.
    expect(wrapper.find('nldd-form').exists()).toBe(false);
    expect(wrapper.find('nldd-rich-text').exists()).toBe(false);
    expect(wrapper.find('h2').exists()).toBe(false);
  });

  it('sets the indicator to complete once the data has arrived', async () => {
    const wrapper = await loadedWrapper();

    expect(wrapper.find('nldd-activity-indicator').attributes('complete')).toBeDefined();
    expect(wrapper.find('h2').exists()).toBe(true);
  });

  it('puts the title first and the danger zone last, for a site admin', async () => {
    const wrapper = await loadedWrapper();

    expect(wrapper.findAll('h2').map((h) => h.text())).toEqual(['Titel van de site', 'Gevarenzone']);
    const sections = wrapper.findAll('nldd-container > section');
    expect(sections.map((s) => s.attributes('aria-labelledby'))).toEqual([
      'heading-site-title',
      'heading-danger-zone',
    ]);
  });

  it('shows an error message on a server error', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());

    const wrapper = await loadedWrapper();

    expect(wrapper.html()).toContain('Serverfout');
    expect(wrapper.find('nldd-form').exists()).toBe(false);
  });

  it('shows a 404 message for an unknown site', async () => {
    const wrapper = await loadedWrapper('bestaat-niet');

    expect(wrapper.html()).toContain('Onbekende site');
    expect(wrapper.find('[data-testid="delete-site"]').exists()).toBe(false);
  });

  it('loads the other site when the route changes under it', async () => {
    backend.data.sites.push({ ...backend.data.sites[0]!, slug: 'andere', title: 'Een andere site' });
    const wrapper = await loadedWrapper();
    expect(wrapper.find('[data-testid="site-title"]').attributes('value')).toBe(
      'Team Aurora website',
    );

    await wrapper.setProps({ site: 'andere' });
    await untilIdle();

    expect(wrapper.find('[data-testid="site-title"]').attributes('value')).toBe('Een andere site');
  });

  it('carries on without the session, as a reader of the title', async () => {
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/me') ? serverErrorFetch()(input, init) : backend.fetch(input, init),
    );

    const wrapper = await loadedWrapper();

    expect(wrapper.find('[data-testid="site-title-text"]').text()).toBe('Team Aurora website');
    expect(wrapper.find('[data-testid="site-title-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="delete-site"]').exists()).toBe(false);
  });
});

describe('site TabSettings: title', () => {
  it('gives a site admin a field with the current title and one save button', async () => {
    const wrapper = await loadedWrapper();

    const field = wrapper.find('[data-testid="site-title"]');
    expect(field.element.tagName.toLowerCase()).toBe('nldd-text-field');
    expect(field.attributes('name')).toBe('site-title');
    expect(field.attributes('value')).toBe('Team Aurora website');
    expect(field.attributes('required')).toBeDefined();
    expect(field.attributes('autocomplete')).toBe('off');
    expect(field.attributes('invalid')).toBeUndefined();
    // nldd-form-field links its label and validation list to direct children.
    expect(field.element.parentElement?.tagName.toLowerCase()).toBe('nldd-form-field');
    expect(field.element.parentElement?.getAttribute('label')).toBe('Titel');

    const buttons = wrapper.findAll('nldd-form nldd-button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]!.attributes('text')).toBe('Bewaar titel');
    expect(buttons[0]!.attributes('type')).toBe('submit');
    expect(buttons[0]!.attributes('variant')).toBe('primary');
    expect(buttons[0]!.attributes('disabled')).toBeUndefined();
    expect(wrapper.find('nldd-form-section').exists()).toBe(false);
    expect(wrapper.find('[data-testid="site-title-text"]').exists()).toBe(false);
  });

  it('has the status line in the page from the first render of the form, empty, for a screen reader to watch', async () => {
    const wrapper = await loadedWrapper();

    const line = statusLine(wrapper);
    expect(line.exists()).toBe(true);
    expect(line.attributes('role')).toBe('status');
    expect(line.text()).toBe('');
  });

  it('judges the empty field with a requirement and keeps the server verdict empty', async () => {
    const wrapper = await loadedWrapper();

    const required = wrapper.find('nldd-validation-item#site-title-required');
    expect(required.text()).toBe('Een titel is nodig');
    expect(required.attributes('required')).toBeDefined();
    expect(serverText(wrapper)).toBe('');
  });

  it('states the limit before anything is typed, as a hint that judges nothing itself', async () => {
    const wrapper = await loadedWrapper();

    const hint = wrapper.find('nldd-validation-item#site-title-length');
    expect(hint.text()).toBe('Een titel is hoogstens 200 tekens lang');
    expect(hint.attributes('hint')).toBeDefined();
    // The server counts, so the field does not hold the title to a rule of its own.
    expect(hint.attributes('maxlength')).toBeUndefined();
    expect(hint.attributes('match')).toBeUndefined();
    expect(wrapper.find('[data-testid="site-title"]').attributes('maxlength')).toBeUndefined();
  });

  it('saves the typed title through the real plak.setSiteTitle and tells the page', async () => {
    const wrapper = await loadedWrapper();

    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    expect(backend.data.sites[0]!.title).toBe('Documentatie');
    // The page refreshes its header and document title on this.
    expect(wrapper.emitted('changed')).toHaveLength(1);
    expect(wrapper.emitted('removed')).toBeUndefined();
    // A line in the page, not a notification that comes and goes.
    expect(statusLine(wrapper).text()).toBe('Titel opgeslagen. De site heet nu Documentatie.');
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('shows the title the server saved, not the one that was typed', async () => {
    const wrapper = await loadedWrapper();

    typeTitle(wrapper, '  Documentatie  ');
    await submitTitle(wrapper);

    expect(wrapper.find('[data-testid="site-title"]').attributes('value')).toBe('Documentatie');
    expect(statusLine(wrapper).text()).toBe('Titel opgeslagen. De site heet nu Documentatie.');
  });

  it('takes the value of a native input event too', async () => {
    const wrapper = await loadedWrapper();

    const field = wrapper.find('[data-testid="site-title"]').element as HTMLElement & {
      value?: string;
    };
    field.value = 'Via native input';
    field.dispatchEvent(new Event('input'));
    await submitTitle(wrapper);

    expect(backend.data.sites[0]!.title).toBe('Via native input');
  });

  it('knows the new title as the current one afterwards', async () => {
    const wrapper = await loadedWrapper();
    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);
    await submitTitle(wrapper);

    expect(spy).not.toHaveBeenCalled();

    typeTitle(wrapper, 'Team Aurora website');
    await submitTitle(wrapper);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(backend.data.sites[0]!.title).toBe('Team Aurora website');
  });

  it('sends nothing and says that nothing changed when the title is the current one', async () => {
    const wrapper = await loadedWrapper();
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);

    typeTitle(wrapper, '  Team Aurora website ');
    await submitTitle(wrapper);

    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.emitted('changed')).toBeUndefined();
    // The button never does nothing, though there is nothing to save.
    expect(statusLine(wrapper).text()).toBe('De titel is niet gewijzigd.');
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('says it again when the button is pressed again with nothing changed', async () => {
    const wrapper = await loadedWrapper();
    await submitTitle(wrapper);
    const line = statusLine(wrapper).element;
    expect(line.textContent?.trim()).toBe('De titel is niet gewijzigd.');

    // A live region only speaks when its words change, so they go and come back.
    const changes: MutationRecord[] = [];
    const observer = new MutationObserver((records) => changes.push(...records));
    observer.observe(line, { childList: true, characterData: true, subtree: true });
    await submitTitle(wrapper);
    observer.disconnect();

    expect(line.textContent?.trim()).toBe('De titel is niet gewijzigd.');
    expect(changes.some((change) => change.removedNodes.length > 0)).toBe(true);
    expect(changes.some((change) => change.addedNodes.length > 0)).toBe(true);
  });

  it('takes the line back when the title is edited', async () => {
    const wrapper = await loadedWrapper();
    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);
    expect(statusLine(wrapper).text()).not.toBe('');

    typeTitle(wrapper, 'Doc');
    await untilIdle();

    expect(statusLine(wrapper).text()).toBe('');
  });

  it('leaves the line empty while the next save is on its way', async () => {
    const wrapper = await loadedWrapper();
    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);
    expect(statusLine(wrapper).text()).not.toBe('');

    vi.stubGlobal('fetch', () => new Promise<Response>(() => {}));
    typeTitle(wrapper, 'Handleiding');
    await wrapper.find('[data-testid="site-title-form"]').trigger('submit');

    expect(statusLine(wrapper).text()).toBe('');
  });

  it('sends the title once while the first save is still on its way', async () => {
    const wrapper = await loadedWrapper();
    let finish: (response: Response) => void = () => {};
    const spy = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    vi.stubGlobal('fetch', spy);

    typeTitle(wrapper, 'Documentatie');
    await wrapper.find('[data-testid="site-title-form"]').trigger('submit');
    await wrapper.find('[data-testid="site-title-form"]').trigger('submit');

    expect(spy).toHaveBeenCalledTimes(1);
    expect(wrapper.find('[data-testid="site-title-save"]').attributes('loading')).toBeDefined();

    finish(
      new Response(JSON.stringify({ ...backend.data.sites[0]!, title: 'Documentatie' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    );
    await untilIdle();

    expect(wrapper.find('[data-testid="site-title-save"]').attributes('loading')).toBeUndefined();
    expect(statusLine(wrapper).text()).toBe('Titel opgeslagen. De site heet nu Documentatie.');
  });

  describe.each([
    ['   ', 'FIELD_EMPTY', 'Een titel is nodig'],
    ['a'.repeat(201), 'FIELD_TOO_LONG', 'Een titel is hoogstens 200 tekens lang'],
    [
      'Doc\tumentatie',
      'FIELD_CONTROL_CHARACTERS',
      'Een titel bevat geen onzichtbare tekens of regeleinden, die vaak meekomen met gekopieerde tekst',
    ],
  ])('when the server refuses %j with %s', (typed, _code, text) => {
    it('says what is wrong at the field, in the words of the interface', async () => {
      const wrapper = await loadedWrapper();

      typeTitle(wrapper, typed);
      await submitTitle(wrapper);

      const field = wrapper.find('[data-testid="site-title"]');
      expect(field.attributes('invalid')).toBeDefined();
      expect(field.attributes('unmet')).toBe('site-title-server');
      expect(serverText(wrapper)).toBe(text);
      // The server's own sentence is not what the member reads.
      expect(wrapper.html()).not.toContain('mag niet leeg zijn');
      expect(wrapper.html()).not.toContain('mag hoogstens');
      expect(wrapper.html()).not.toContain('stuur- of opmaaktekens');
      expect(wrapper.find('nldd-notification').exists()).toBe(false);
      expect(wrapper.emitted('changed')).toBeUndefined();
      expect(backend.data.sites[0]!.title).toBe('Team Aurora website');
    });

    it('keeps what was typed and moves the focus to the field', async () => {
      const wrapper = await loadedWrapper();
      const focus = vi.spyOn(HTMLElement.prototype, 'focus');

      typeTitle(wrapper, typed);
      await submitTitle(wrapper);

      expect(wrapper.find('[data-testid="site-title"]').attributes('value')).toBe(typed);
      expect(focus).toHaveBeenCalledTimes(1);
      expect(focus.mock.contexts[0]).toBe(wrapper.find('[data-testid="site-title"]').element);
      focus.mockRestore();
    });

    it('says the verdict aloud too, which is all there is to hear when Enter was pressed in the field', async () => {
      const wrapper = await loadedWrapper('website', true);
      const field = wrapper.find('[data-testid="site-title"]').element as HTMLElement;
      // Pressing Enter submits from the field, which has the focus before the answer comes.
      field.setAttribute('tabindex', '0');
      field.focus();
      expect(document.activeElement).toBe(field);

      typeTitle(wrapper, typed);
      await submitTitle(wrapper);

      // Moving the focus to where it already is says nothing; the alert does.
      expect(document.activeElement).toBe(field);
      const alert = wrapper.find('[role="alert"]');
      expect(alert.text()).toBe(text);
      // The same words are on screen at the field already.
      expect(alert.classes()).toContain('visually-hidden');
    });
  });

  it('says nothing aloud while nothing is refused', async () => {
    const wrapper = await loadedWrapper();
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);

    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });

  it('says it again when the same refusal comes back, and takes the alert back on an edit', async () => {
    const wrapper = await loadedWrapper();
    typeTitle(wrapper, '   ');
    await submitTitle(wrapper);
    const first = wrapper.find('[role="alert"]').element;

    // A live region only speaks when something new appears in it.
    await submitTitle(wrapper);
    const second = wrapper.find('[role="alert"]').element;
    expect(second).not.toBe(first);
    expect(second.textContent?.trim()).toBe('Een titel is nodig');

    typeTitle(wrapper, 'Doc');
    await untilIdle();
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });

  it('leaves the alert to a refusal about the text: another failure is a notification', async () => {
    const wrapper = await loadedWrapper();
    vi.stubGlobal('fetch', serverErrorFetch());

    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    expect(wrapper.find('nldd-notification[text="Titel niet opgeslagen"]').exists()).toBe(true);
  });

  it('shows the SPA text for the code the server named, whatever the server wrote', async () => {
    const wrapper = await loadedWrapper();
    vi.stubGlobal(
      'fetch',
      problemFetch(422, {
        title: 'Unprocessable',
        detail: 'Field "title" must not be empty.',
        code: 'FIELD_EMPTY',
      }),
    );

    typeTitle(wrapper, 'Nieuw');
    await submitTitle(wrapper);

    expect(serverText(wrapper)).toBe('Een titel is nodig');
  });

  it('takes the verdict back as soon as the title is edited', async () => {
    const wrapper = await loadedWrapper();
    typeTitle(wrapper, '   ');
    await submitTitle(wrapper);
    expect(wrapper.find('[data-testid="site-title"]').attributes('invalid')).toBeDefined();

    typeTitle(wrapper, 'Doc');
    await untilIdle();

    const field = wrapper.find('[data-testid="site-title"]');
    expect(field.attributes('invalid')).toBeUndefined();
    expect(field.attributes('unmet')).toBeUndefined();
    expect(serverText(wrapper)).toBe('');
  });

  it('lets a corrected title through after a refusal', async () => {
    const wrapper = await loadedWrapper();
    typeTitle(wrapper, '   ');
    await submitTitle(wrapper);

    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    expect(backend.data.sites[0]!.title).toBe('Documentatie');
    expect(wrapper.find('[data-testid="site-title"]').attributes('invalid')).toBeUndefined();
    expect(statusLine(wrapper).text()).toBe('Titel opgeslagen. De site heet nu Documentatie.');
  });

  it('leaves the line empty when the save is refused, the verdict being elsewhere', async () => {
    const wrapper = await loadedWrapper();
    typeTitle(wrapper, '   ');
    await submitTitle(wrapper);

    expect(statusLine(wrapper).text()).toBe('');

    vi.stubGlobal('fetch', serverErrorFetch());
    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    expect(statusLine(wrapper).text()).toBe('');
  });

  it('reports a refusal that is not about the text as a critical notice, with the field left clean', async () => {
    const wrapper = await loadedWrapper();
    vi.stubGlobal(
      'fetch',
      problemFetch(429, {
        title: 'Te veel verzoeken',
        detail: 'Probeer het over een minuut opnieuw.',
        code: 'RATE_LIMITED',
      }),
    );

    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    const notice = wrapper.find('nldd-notification[text="Titel niet opgeslagen"]');
    expect(notice.attributes('variant')).toBe('critical');
    expect(notice.attributes('supporting-text')).toBe('Probeer het over een minuut opnieuw.');
    expect(wrapper.find('[data-testid="site-title"]').attributes('invalid')).toBeUndefined();
    expect(wrapper.find('[data-testid="site-title"]').attributes('value')).toBe('Documentatie');
    expect(wrapper.emitted('changed')).toBeUndefined();
  });

  it('reports the title of a problem without a detail', async () => {
    const wrapper = await loadedWrapper();
    vi.stubGlobal('fetch', serverErrorFetch());

    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    expect(
      wrapper.find('nldd-notification[text="Titel niet opgeslagen"]').attributes('supporting-text'),
    ).toBe('Serverfout');
  });

  it('reports a refusal by the role as a critical notice', async () => {
    const wrapper = await loadedWrapper();
    // The role was taken away in the meantime: the server has the last word.
    backend.data.loggedInMemberId = 'lid-3';

    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    const notice = wrapper.find('nldd-notification[text="Titel niet opgeslagen"]');
    expect(notice.attributes('variant')).toBe('critical');
    expect(notice.attributes('supporting-text')).toBe(
      'Hiervoor heb je minimaal de rol beheerder nodig.',
    );
  });

  it('reports a generic failure when saving throws something other than an ApiError', async () => {
    const wrapper = await loadedWrapper();
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    expect(
      wrapper.find('nldd-notification[text="Titel niet opgeslagen"]').attributes('supporting-text'),
    ).toBe('Opslaan is niet gelukt.');
  });

  it('can save again after a failure', async () => {
    const wrapper = await loadedWrapper();
    vi.stubGlobal('fetch', serverErrorFetch());
    typeTitle(wrapper, 'Documentatie');
    await submitTitle(wrapper);

    vi.stubGlobal('fetch', backend.fetch);
    await submitTitle(wrapper);

    expect(backend.data.sites[0]!.title).toBe('Documentatie');
  });
});

describe('site TabSettings: who sees what', () => {
  it('shows the title as text, and who may change it, to an editor of the group', async () => {
    // lid-3 (Ada Vermeer) is editor in team-aurora and has no site role.
    backend.data.loggedInMemberId = 'lid-3';

    const wrapper = await loadedWrapper();

    expect(wrapper.find('[data-testid="site-title-form"]').exists()).toBe(false);
    expect(wrapper.find('nldd-text-field').exists()).toBe(false);
    const title = wrapper.find('[data-testid="site-title-text"]');
    expect(title.text()).toBe('Team Aurora website');
    expect(title.element.closest('nldd-rich-text')).not.toBeNull();
    expect(wrapper.find('section[aria-labelledby="heading-site-title"]').text()).toContain(
      'Alleen een beheerder van de site kan de titel wijzigen.',
    );
  });

  it('keeps the danger zone away from everyone but a site admin', async () => {
    backend.data.loggedInMemberId = 'lid-3';

    const wrapper = await loadedWrapper();

    expect(wrapper.findAll('h2').map((h) => h.text())).toEqual(['Titel van de site']);
    expect(wrapper.find('nldd-box[background="critical"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="delete-site"]').exists()).toBe(false);
    expect(wrapper.findComponent(ConfirmModal).exists()).toBe(false);
  });

  it('treats a reader of the site the same', async () => {
    // lid-2 (Wim Weg) holds an editor role on this one site, and none in the group.
    backend.data.loggedInMemberId = 'lid-2';

    const wrapper = await loadedWrapper();

    expect(wrapper.find('[data-testid="site-title-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="delete-site"]').exists()).toBe(false);
  });

  it('gives an admin of this site alone the form and the danger zone', async () => {
    // lid-4 (Zoë de Wit) is a reader in the group and admin of this site.
    backend.data.loggedInMemberId = 'lid-4';

    const wrapper = await loadedWrapper();

    expect(wrapper.find('[data-testid="site-title-form"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="delete-site"]').exists()).toBe(true);
  });

  it('gives a platform admin without a role on the site nothing extra', async () => {
    // The backend has no platform admin bypass here, so neither does the page.
    backend.data.groupMembers = backend.data.groupMembers.filter((l) => l.memberId !== 'lid-1');

    const wrapper = await loadedWrapper();

    expect(wrapper.find('[data-testid="site-title-form"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="site-title-text"]').text()).toBe('Team Aurora website');
    expect(wrapper.find('[data-testid="delete-site"]').exists()).toBe(false);
  });
});

describe('site TabSettings: danger zone', () => {
  it('deletes the site only after explicit confirmation', async () => {
    const wrapper = await loadedWrapper();

    // Confirming while the dialog is not open does nothing.
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();
    expect(backend.data.sites).toHaveLength(1);
    expect(wrapper.emitted('removed')).toBeFalsy();

    // Open the dialog, type the address and confirm: now the site really
    // disappears.
    await wrapper.find('[data-testid="delete-site"]').trigger('click');
    typeAddress(wrapper);
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(backend.data.sites).toHaveLength(0);
    expect(backend.data.versions).toHaveLength(0);
    expect(backend.data.previews).toHaveLength(0);
    expect(wrapper.emitted('removed')).toHaveLength(1);
    expect(wrapper.emitted('changed')).toBeUndefined();
  });

  it('asks for the address of the site and deletes nothing on a click alone', async () => {
    const wrapper = await loadedWrapper();

    await wrapper.find('[data-testid="delete-site"]').trigger('click');
    expect(wrapper.findComponent(ConfirmModal).props('confirmPhrase')).toBe('team-aurora/website');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    typeAddress(wrapper, 'website');
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    expect(backend.data.sites).toHaveLength(1);
    expect(wrapper.emitted('removed')).toBeFalsy();
    expect(wrapper.find('[data-testid="confirm-phrase"]').attributes('invalid')).toBeDefined();
  });

  it('the safe way out is at the top and is the primary button', async () => {
    const wrapper = await loadedWrapper();

    await wrapper.find('[data-testid="delete-site"]').trigger('click');
    const actions = wrapper.findAll('nldd-modal-dialog nldd-button');
    expect(actions[0]!.attributes('data-testid')).toBe('confirm-cancel');
    expect(actions[0]!.attributes('variant')).toBe('primary');
    expect(actions[0]!.attributes('text')).toBe('Behoud site');
    expect(actions[1]!.attributes('data-testid')).toBe('confirm-continue');
    expect(actions[1]!.attributes('variant')).toBe('destructive');
    expect(actions[1]!.attributes('disabled')).toBeUndefined();
  });

  it('names the site in the question the dialog asks', async () => {
    const wrapper = await loadedWrapper();

    await wrapper.find('[data-testid="delete-site"]').trigger('click');

    expect(wrapper.findComponent(ConfirmModal).props('title')).toBe(
      'Site team-aurora/website verwijderen?',
    );
  });

  it('Behoud site closes the dialog without deleting', async () => {
    const wrapper = await loadedWrapper();

    await wrapper.find('[data-testid="delete-site"]').trigger('click');
    await wrapper.find('[data-testid="confirm-cancel"]').trigger('click');
    await untilIdle();

    expect(backend.data.sites).toHaveLength(1);
    expect(wrapper.emitted('removed')).toBeFalsy();
    expect(wrapper.findComponent(ConfirmModal).props('open')).toBe(false);
  });

  it('closes the dialog and reports it when deleting fails', async () => {
    const wrapper = await loadedWrapper();

    await wrapper.find('[data-testid="delete-site"]').trigger('click');
    vi.stubGlobal('fetch', serverErrorFetch());
    typeAddress(wrapper);
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    // The modal renders the page below it inert: the notification can only be
    // read once the dialog is closed.
    expect(wrapper.findComponent(ConfirmModal).props('open')).toBe(false);
    const notice = wrapper.find('nldd-notification[text="Site niet verwijderd"]');
    expect(notice.exists()).toBe(true);
    expect(notice.attributes('variant')).toBe('critical');
    expect(notice.attributes('supporting-text')).toBe('Serverfout');
    expect(wrapper.emitted('removed')).toBeFalsy();
    expect(wrapper.find('[data-testid="confirm-continue"]').attributes('loading')).toBeUndefined();
  });

  it('reports a generic failure when deleting throws something other than an ApiError', async () => {
    const wrapper = await loadedWrapper();

    await wrapper.find('[data-testid="delete-site"]').trigger('click');
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));
    typeAddress(wrapper);
    await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
    await untilIdle();

    const notice = wrapper.find('nldd-notification[text="Site niet verwijderd"]');
    expect(notice.attributes('supporting-text')).toBe('Verwijderen is niet gelukt.');
  });

  it('puts the danger zone in an nldd-box with a critical background', async () => {
    const wrapper = await loadedWrapper();

    const box = wrapper.find('nldd-box[background="critical"]');
    expect(box.attributes('background')).toBe('critical');
    expect(box.find('nldd-container').exists()).toBe(true);
    expect(box.text()).toContain('Gevarenzone');
  });
});
