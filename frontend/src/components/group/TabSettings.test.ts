import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, type MockBackend } from '../../../tests/mock-api';
import type { Access, Site } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import { fireDetailEvent, problemFetch, serverErrorFetch } from '@/components/site/testHelpers';
import { AUTOSAVE_PAUSE_MS } from '@/composables/autosavedText';
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
    canDelete?: boolean;
    canRename?: boolean;
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
      canDelete: props.canDelete ?? false,
      canRename: props.canRename ?? false,
    },
    global: { stubs: { teleport: true } },
    attachTo: props.attach ? document.body : undefined,
  });
}

function fireChange(el: Element, checked: boolean): void {
  el.dispatchEvent(new CustomEvent('change', { detail: { checked } }));
}

describe('group TabSettings', () => {
  it('puts the exceptions under their own heading, with no summary that repeats the choice', () => {
    const wrapper = makeWrapper();
    const html = wrapper.html();

    const heading = wrapper.find('h3#heading-default-access-exceptions');
    expect(heading.text()).toBe('Uitzonderingen');
    expect(wrapper.find('nldd-title:has(h3) [slot="subtitle"]').text()).toBe(
      'Deze twee staan los van de keuze hierboven en los van elkaar. Ze laten er mensen bij, en nemen nooit iemand weg.',
    );
    expect(wrapper.find('nldd-inline-dialog').exists()).toBe(false);
    expect(html.indexOf('heading-default-access-exceptions')).toBeGreaterThan(
      html.indexOf('default-access-group'),
    );
    expect(html.indexOf('default-access-keys')).toBeGreaterThan(
      html.indexOf('heading-default-access-exceptions'),
    );
    expect(html.indexOf('default-access-invitees')).toBeGreaterThan(
      html.indexOf('default-access-keys'),
    );
  });

  it('warns under the exceptions when nobody can view a new site, until one is on', async () => {
    const wrapper = makeWrapper({ access: { base: 'nobody', keys: false, invitees: false } });

    const warning = wrapper.find('[data-testid="default-access-nobody-warning"]');
    expect(warning.attributes('variant')).toBe('warning');
    expect(warning.attributes('text')).toBe('Niemand kan de site bekijken');
    expect(warning.attributes('supporting-text')).toBe(
      'Zet hieronder geheime links of genodigden aan om iemand binnen te laten.',
    );
    const html = wrapper.html();
    // Under the choice it follows from, above the exceptions that lift it.
    expect(html.indexOf('default-access-nobody-warning')).toBeGreaterThan(
      html.indexOf('default-access-group'),
    );
    expect(html.indexOf('default-access-nobody-warning')).toBeLessThan(
      html.indexOf('heading-default-access-exceptions'),
    );

    fireChange(wrapper.find('[data-testid="default-access-keys"]').element, true);
    await flushPromises();
    expect(wrapper.find('[data-testid="default-access-nobody-warning"]').exists()).toBe(false);
  });

  it('does not warn while the address lets somebody in', () => {
    const wrapper = makeWrapper({ access: { base: 'site_team', keys: false, invitees: false } });

    expect(wrapper.find('[data-testid="default-access-nobody-warning"]').exists()).toBe(false);
  });

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

  /** The field loses the focus: `change`. */
  async function commitName(wrapper: ReturnType<typeof makeWrapper>): Promise<void> {
    wrapper.find('[data-testid="group-name"]').element.dispatchEvent(new Event('change'));
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

  it('gives a group admin a field with the current name and no save button', () => {
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

    expect(wrapper.find('nldd-form nldd-button').exists()).toBe(false);
    expect(wrapper.find('[data-testid="group-name-save"]').exists()).toBe(false);
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

  it('judges nothing before anything happens, and the emptied field only once it is committed', async () => {
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);
    const wrapper = makeWrapper({ canRename: true });
    expect(serverText(wrapper)).toBe('');
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);

    typeName(wrapper, '');
    await flushPromises();
    expect(serverText(wrapper)).toBe('');
    expect(wrapper.find('[data-testid="group-name"]').attributes('invalid')).toBeUndefined();

    await commitName(wrapper);

    const field = wrapper.find('[data-testid="group-name"]');
    expect(field.attributes('invalid')).toBeDefined();
    expect(field.attributes('unmet')).toBe('group-settings-name-server');
    expect(serverText(wrapper)).toBe('Een naam is nodig');
    expect(wrapper.find('[role="alert"]').text()).toBe('Een naam is nodig');
    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.emitted('groupChanged')).toBeUndefined();
  });

  it('shows no length requirement before the name is too long', () => {
    const wrapper = makeWrapper({ canRename: true });

    expect(wrapper.html()).not.toContain('hoogstens 200 tekens');
    expect(wrapper.find('nldd-validation-item[hint]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="group-name"]').attributes('invalid')).toBeUndefined();
  });

  describe('when the typed name is longer than 200 characters', () => {
    it('judges the field while typing, without a save', async () => {
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, 'a'.repeat(201));
      await flushPromises();

      const field = wrapper.find('[data-testid="group-name"]');
      expect(field.attributes('invalid')).toBeDefined();
      expect(field.attributes('unmet')).toBe('group-settings-name-server');
      expect(serverText(wrapper)).toBe('Een naam is hoogstens 200 tekens lang');
      expect(wrapper.find('[role="alert"]').text()).toBe('Een naam is hoogstens 200 tekens lang');
    });

    it('lets go of the verdict once the name is short enough again', async () => {
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, 'a'.repeat(201));
      await flushPromises();
      typeName(wrapper, 'a'.repeat(200));
      await flushPromises();

      expect(wrapper.find('[data-testid="group-name"]').attributes('invalid')).toBeUndefined();
      expect(serverText(wrapper)).toBe('');
      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    });

    it('counts as the server does: characters, not UTF-16 units, after trimming', async () => {
      const wrapper = makeWrapper({ canRename: true });

      // 200 emoji are 400 UTF-16 units, and the spaces are trimmed off.
      typeName(wrapper, `  ${'🌅'.repeat(200)}  `);
      await flushPromises();

      expect(wrapper.find('[data-testid="group-name"]').attributes('invalid')).toBeUndefined();
      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    });

    it('sends nothing on change, on Enter or after the pause', async () => {
      vi.useFakeTimers();
      try {
        const spy = vi.fn(backend.fetch);
        vi.stubGlobal('fetch', spy);
        const wrapper = makeWrapper({ canRename: true });

        typeName(wrapper, 'a'.repeat(201));
        await commitName(wrapper);
        await submitName(wrapper);
        await vi.advanceTimersByTimeAsync(AUTOSAVE_PAUSE_MS + 1000);

        expect(spy).not.toHaveBeenCalled();
        expect(wrapper.emitted('groupChanged')).toBeUndefined();
      } finally {
        vi.useRealTimers();
      }
    });
  });

  it('says the limit at the field when the server refuses a name as too long', async () => {
    vi.stubGlobal(
      'fetch',
      problemFetch(422, { title: 'Onverwerkbare invoer', code: 'FIELD_TOO_LONG' }),
    );
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, 'Team Zonsopgang');
    await submitName(wrapper);

    expect(serverText(wrapper)).toBe('Een naam is hoogstens 200 tekens lang');
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

  describe('autosave', () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    it('saves after a pause in typing, not before', async () => {
      vi.useFakeTimers();
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, 'Team Zonsopgang');
      await vi.advanceTimersByTimeAsync(AUTOSAVE_PAUSE_MS - 1);
      expect(backend.data.groups[0]!.name).toBe('Team Aurora');
      expect(wrapper.emitted('groupChanged')).toBeUndefined();

      await vi.advanceTimersByTimeAsync(1);

      expect(backend.data.groups[0]!.name).toBe('Team Zonsopgang');
      expect(wrapper.emitted('groupChanged')).toHaveLength(1);
      expect(statusLine(wrapper).text()).toBe('Naam opgeslagen.');
    });

    it('starts the pause anew with every keystroke, and saves the last text once', async () => {
      vi.useFakeTimers();
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, 'Team Z');
      await vi.advanceTimersByTimeAsync(AUTOSAVE_PAUSE_MS - 1);
      typeName(wrapper, 'Team Zon');
      await vi.advanceTimersByTimeAsync(AUTOSAVE_PAUSE_MS - 1);
      expect(wrapper.emitted('groupChanged')).toBeUndefined();

      await vi.advanceTimersByTimeAsync(1);

      expect(wrapper.emitted('groupChanged')).toHaveLength(1);
      expect(backend.data.groups[0]!.name).toBe('Team Zon');
    });

    it('saves at once when the field loses the focus', async () => {
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, 'Team Zonsopgang');
      expect(backend.data.groups[0]!.name).toBe('Team Aurora');
      await commitName(wrapper);

      expect(backend.data.groups[0]!.name).toBe('Team Zonsopgang');
      expect(wrapper.emitted('groupChanged')).toHaveLength(1);
    });

    it('saves at once on Enter, and not a second time after the pause', async () => {
      vi.useFakeTimers();
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, 'Team Zonsopgang');
      await wrapper.find('[data-testid="group-name-form"]').trigger('submit');
      await vi.advanceTimersByTimeAsync(0);
      expect(backend.data.groups[0]!.name).toBe('Team Zonsopgang');

      await vi.advanceTimersByTimeAsync(AUTOSAVE_PAUSE_MS + 1000);

      expect(wrapper.emitted('groupChanged')).toHaveLength(1);
    });

    it('sends a pending text when the page is left', async () => {
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, 'Team Zonsopgang');
      window.dispatchEvent(new Event('pagehide'));
      await flushPromises();

      expect(backend.data.groups[0]!.name).toBe('Team Zonsopgang');
      wrapper.unmount();
    });

    it('sends a pending text when the tab is closed', async () => {
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, 'Team Zonsopgang');
      wrapper.unmount();
      await flushPromises();

      expect(backend.data.groups[0]!.name).toBe('Team Zonsopgang');
    });

    it('sends nothing when the tab is closed with nothing pending', async () => {
      const spy = vi.fn(backend.fetch);
      vi.stubGlobal('fetch', spy);
      const wrapper = makeWrapper({ canRename: true });

      wrapper.unmount();
      await flushPromises();

      expect(spy).not.toHaveBeenCalled();
    });

    it('sends the pending text to the group it was typed on when the tab is reused for another', async () => {
      backend.data.groups.push({
        slug: 'team-zon',
        name: 'Team Zon',
        defaultAccess: access,
      } as (typeof backend.data.groups)[number]);
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, 'Team Zonsopgang');
      await wrapper.setProps({ group: 'team-zon', groupName: 'Team Zon' });
      await flushPromises();

      expect(backend.data.groups.find((g) => g.slug === 'team-aurora')!.name).toBe('Team Zonsopgang');
      expect(backend.data.groups.find((g) => g.slug === 'team-zon')!.name).toBe('Team Zon');
      expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe('Team Zon');
      expect(statusLine(wrapper).text()).toBe('');
    });

    it('shows the new name and sends nothing when the tab is reused with nothing pending', async () => {
      const spy = vi.fn(backend.fetch);
      vi.stubGlobal('fetch', spy);
      const wrapper = makeWrapper({ canRename: true });

      await wrapper.setProps({ group: 'team-zon', groupName: 'Team Zon' });
      await flushPromises();

      expect(spy).not.toHaveBeenCalled();
      expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe('Team Zon');
    });
  });

  it('puts a check mark in the field after a save, and takes it away on the next keystroke', async () => {
    const wrapper = makeWrapper({ canRename: true });
    const field = wrapper.find('[data-testid="group-name"]');
    expect(field.attributes('valid')).toBeUndefined();

    typeName(wrapper, 'Team Zonsopgang');
    await commitName(wrapper);
    await flushPromises();
    expect(field.attributes('valid')).toBeDefined();
    // Said to a screen reader only: the check mark is what a sighted member sees.
    const notice = statusLine(wrapper);
    expect(notice.text()).toBe('Naam opgeslagen.');
    expect(notice.classes()).toContain('visually-hidden');

    typeName(wrapper, 'Team Zonsopgang 2');
    await flushPromises();
    expect(field.attributes('valid')).toBeUndefined();
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
    expect(statusLine(wrapper).text()).toBe('Naam opgeslagen.');
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('shows the name the server saved, not the one that was typed', async () => {
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, '  Team Zonsopgang  ');
    await submitName(wrapper);

    expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe('Team Zonsopgang');
    expect(statusLine(wrapper).text()).toBe('Naam opgeslagen.');
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

  it('sends nothing and says nothing when the name is the current one', async () => {
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);
    const wrapper = makeWrapper({ canRename: true });

    typeName(wrapper, '  Team Aurora ');
    await commitName(wrapper);

    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.emitted('groupChanged')).toBeUndefined();
    expect(statusLine(wrapper).text()).toBe('');
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
  });

  it('sends nothing on a second commit when nothing changed since the save', async () => {
    const wrapper = makeWrapper({ canRename: true });
    typeName(wrapper, 'Team Zonsopgang');
    await commitName(wrapper);
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);

    await commitName(wrapper);
    await submitName(wrapper);

    expect(spy).not.toHaveBeenCalled();
    expect(wrapper.emitted('groupChanged')).toHaveLength(1);
    expect(statusLine(wrapper).text()).toBe('Naam opgeslagen.');
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

  it('sends the name once while the first save is still on its way, then the newer text', async () => {
    const finishers: Array<(response: Response) => void> = [];
    const spy = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          finishers.push(resolve);
        }),
    );
    vi.stubGlobal('fetch', spy);
    const wrapper = makeWrapper({ canRename: true });
    const reply = (name: string) =>
      new Response(JSON.stringify({ slug: 'team-aurora', name, defaultAccess: access }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });

    typeName(wrapper, 'Team Zonsopgang');
    await commitName(wrapper);
    await commitName(wrapper);
    await submitName(wrapper);
    expect(spy).toHaveBeenCalledTimes(1);

    typeName(wrapper, 'Team Horizon');
    await commitName(wrapper);
    expect(spy).toHaveBeenCalledTimes(1);

    finishers[0]!(reply('Team Zonsopgang'));
    await flushPromises();

    expect(spy).toHaveBeenCalledTimes(2);
    expect(JSON.parse(String((spy.mock.calls[1] as unknown as [string, RequestInit])[1].body))).toMatchObject({
      name: 'Team Horizon',
    });
    // What was typed meanwhile stays in the field.
    expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe('Team Horizon');

    finishers[1]!(reply('Team Horizon'));
    await flushPromises();

    expect(statusLine(wrapper).text()).toBe('Naam opgeslagen.');
  });

  it('does not send the first text a second time when nothing newer was typed', async () => {
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
    await commitName(wrapper);
    await commitName(wrapper);
    finish(
      new Response(
        JSON.stringify({ slug: 'team-aurora', name: 'Team Zonsopgang', defaultAccess: access }),
        { status: 200, headers: { 'content-type': 'application/json' } },
      ),
    );
    await flushPromises();

    expect(spy).toHaveBeenCalledTimes(1);
    expect(statusLine(wrapper).text()).toBe('Naam opgeslagen.');
  });

  describe.each([
    ['   ', 'FIELD_EMPTY', 'Een naam is nodig'],
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

    it('keeps what was typed and leaves the focus where it was', async () => {
      const focus = vi.spyOn(HTMLElement.prototype, 'focus');
      const wrapper = makeWrapper({ canRename: true });

      typeName(wrapper, typed);
      await submitName(wrapper);

      expect(wrapper.find('[data-testid="group-name"]').attributes('value')).toBe(typed);
      expect(focus).not.toHaveBeenCalled();
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

  it('keeps the verdict through a second commit, sends nothing, and takes the alert back on an edit', async () => {
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);
    const wrapper = makeWrapper({ canRename: true });
    typeName(wrapper, '   ');
    await commitName(wrapper);
    expect(wrapper.find('[role="alert"]').text()).toBe('Een naam is nodig');

    await commitName(wrapper);
    expect(wrapper.find('[role="alert"]').text()).toBe('Een naam is nodig');
    expect(spy).not.toHaveBeenCalled();

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
    expect(statusLine(wrapper).text()).toBe('Naam opgeslagen.');
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

describe('group TabSettings: danger zone', () => {
  function site(slug: string, title = slug): Site {
    return { groupSlug: 'team-aurora', slug, title } as Site;
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
