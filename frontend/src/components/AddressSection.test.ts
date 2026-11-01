import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '../../tests/mock-api';
import * as toon from '@/api/toon';
import type { PreviousSlug, Site } from '@/api/types';
import ConfirmModal from '@/components/ConfirmModal.vue';
import {
  fireDetailEvent,
  problemFetch,
  serverErrorFetch,
  untilIdle,
} from '@/components/site/testHelpers';
import { SLUG_MATCH, SLUG_PATTERN, slugifyTyped } from '@/composables/slug';
import { _setLocaleForTest } from '@/i18n';

import AddressSection from './AddressSection.vue';

let backend: MockBackend;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  // Today is the 8th of October 2026 in Amsterdam: the 30th day after it is the 7th of November.
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(new Date('2026-10-08T10:00:00Z'));
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** Redirects until the end of the 6th of November in Amsterdam. */
const OLD: PreviousSlug = { slug: 'oude-naam', redirectsUntil: '2026-11-06T23:00:00Z' };
const OLDER: PreviousSlug = { slug: 'oudere-naam', redirectsUntil: '2026-10-20T22:00:00Z' };

const NEW_SITE = 'https://sites.plak.test/team-aurora/website/';

interface Props {
  kind: 'group' | 'site';
  group: string;
  site?: string;
  previousSlugs: PreviousSlug[];
  contentBase: string;
  redirectDays: number;
  canChange: boolean;
  siteOnlyAdmin?: boolean;
  isPublic: boolean;
  sites?: Site[];
  siteId?: string;
  siblings?: Site[];
  canRelease?: boolean;
}

function siteProps(extra: Partial<Props> = {}): Props {
  return {
    kind: 'site',
    group: 'team-aurora',
    site: 'website',
    previousSlugs: [],
    contentBase: MOCK_CONTENT_BASE,
    redirectDays: 30,
    canChange: true,
    isPublic: false,
    siteId: backend.data.sites[0]!.id,
    ...extra,
  };
}

function groupProps(extra: Partial<Props> = {}): Props {
  return {
    kind: 'group',
    group: 'team-aurora',
    previousSlugs: [],
    contentBase: MOCK_CONTENT_BASE,
    redirectDays: 30,
    canChange: true,
    isPublic: false,
    sites: backend.data.sites,
    ...extra,
  };
}

function makeWrapper(props: Props) {
  return mount(AddressSection, { props, global: { stubs: { teleport: true } } });
}

type Wrapper = ReturnType<typeof makeWrapper>;

function startButton(wrapper: Wrapper, kind = 'site') {
  return wrapper.find(`[data-testid="${kind}-address-start"]`);
}

async function openForm(wrapper: Wrapper, kind = 'site'): Promise<void> {
  await startButton(wrapper, kind).trigger('click');
  await untilIdle();
}

/** The sheet is gone: its own close button, Escape, or the end of a change. */
async function closeSheet(wrapper: Wrapper): Promise<void> {
  await wrapper.find('nldd-sheet').trigger('close');
  await untilIdle();
}

/** After a change from the sheet: the dialog goes first, and then the sheet. */
async function closeOverlays(wrapper: Wrapper): Promise<void> {
  await closeDialog(wrapper);
  await closeSheet(wrapper);
}

/**
 * With a real teleport, for what a stubbed one cannot show: the stub renders
 * what it holds anew on every render, and the elements with it.
 */
function mountTeleported(props: Props) {
  return mount(AddressSection, { props });
}

/**
 * In this file nldd-sheet is an unknown element: spies on the prototype see
 * what it is asked, and the dialog's calls are left out.
 */
function sheetSpies() {
  const proto = HTMLElement.prototype as unknown as Record<string, unknown>;
  const show = vi.fn();
  const hide = vi.fn();
  proto.show = show;
  proto.hide = hide;
  const onSheet = (spy: typeof show) => (spy.mock.contexts as Element[]).filter((el) => el.tagName === 'NLDD-SHEET');
  return {
    shown: () => onSheet(show).length,
    hidden: () => onSheet(hide).length,
    restore: () => {
      delete proto.show;
      delete proto.hide;
    },
  };
}

/** Mounted with the sheet open, as the start button leaves it. */
async function editing(props: Props): Promise<Wrapper> {
  const wrapper = makeWrapper(props);
  await openForm(wrapper, props.kind);
  return wrapper;
}

function field(wrapper: Wrapper, kind = 'site') {
  return wrapper.find(`[data-testid="${kind}-address"]`);
}

function type(wrapper: Wrapper, value: string, kind = 'site'): void {
  fireDetailEvent(field(wrapper, kind).element, 'input', { value });
}

async function submit(wrapper: Wrapper, kind = 'site'): Promise<void> {
  await wrapper.find(`[data-testid="${kind}-address-form"]`).trigger('submit');
  await untilIdle();
}

async function confirm(wrapper: Wrapper): Promise<void> {
  await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
  await untilIdle();
}

/** What the dialog says once it is gone, the moment the page is ours again. */
async function closeDialog(wrapper: Wrapper): Promise<void> {
  await wrapper.find('nldd-modal-dialog').trigger('close');
  await untilIdle();
}

function dialog(wrapper: Wrapper) {
  return wrapper.findComponent(ConfirmModal);
}

function statusLine(wrapper: Wrapper, kind = 'site') {
  return wrapper.find(`[data-testid="${kind}-address-notice"]`);
}

function serverText(wrapper: Wrapper, kind = 'site'): string {
  return wrapper.find(`nldd-validation-item#${kind}-address-server`).text();
}

describe('AddressSection (site): what everyone sees', () => {
  it('puts the section under its own heading', () => {
    const wrapper = makeWrapper(siteProps());

    expect(wrapper.find('h2').text()).toBe('Adres van de site');
    expect(wrapper.find('section').attributes('aria-labelledby')).toBe('heading-site-address');
    expect(wrapper.find('h2').attributes('id')).toBe('heading-site-address');
  });

  it('says where the site is, as the address it is served at', () => {
    const wrapper = makeWrapper(siteProps());

    const current = wrapper.find('[data-testid="site-address-current"]');
    expect(current.element.closest('nldd-rich-text')).not.toBeNull();
    expect(current.text()).toBe(`Het adres van deze site is ${NEW_SITE}.`);
    const link = current.find('nldd-link');
    expect(link.text()).toBe(NEW_SITE);
    expect(link.attributes('href')).toBe(NEW_SITE);
    expect(link.attributes('target')).toBe('_blank');
    expect(link.attributes('translate')).toBe('no');
    expect(current.find('code').exists()).toBe(false);
  });

  it('puts the button to change it straight under the address, above the old addresses', () => {
    const wrapper = makeWrapper(siteProps({ previousSlugs: [OLD] }));

    const start = wrapper.find('[data-testid="site-address-start"]').element;
    const position = start.compareDocumentPosition(wrapper.find('h3').element);
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('lists no old addresses while there are none', () => {
    const wrapper = makeWrapper(siteProps());

    expect(wrapper.find('[data-testid="site-address-previous"]').exists()).toBe(false);
  });

  it('lists the old addresses that still redirect under a heading of their own, as rows of a list', () => {
    const wrapper = makeWrapper(siteProps({ previousSlugs: [OLD, OLDER] }));

    const heading = wrapper.find('h3');
    expect(heading.text()).toBe('Oude adressen');
    expect(heading.attributes('id')).toBe('heading-site-address-previous');
    expect(heading.element.parentElement?.tagName.toLowerCase()).toBe('nldd-title');
    const list = wrapper.find('[data-testid="site-address-previous"]');
    expect(list.element.tagName.toLowerCase()).toBe('nldd-list');
    expect(list.attributes('type')).toBe('form');
    expect(list.attributes('appearance')).toBe('box-base');
    expect(list.attributes('accessible-label')).toBe('Oude adressen');
    const position = heading.element.compareDocumentPosition(list.element);
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('names each old address in short, in code, with the last day it redirects', () => {
    const wrapper = makeWrapper(siteProps({ previousSlugs: [OLD, OLDER] }));

    const cells = wrapper.findAll('[data-testid="site-address-previous"] nldd-list-item nldd-text-cell');
    expect(cells.map((cell) => cell.find('code').text())).toEqual([
      'team-aurora/oude-naam',
      'team-aurora/oudere-naam',
    ]);
    expect(cells.map((cell) => cell.attributes('supporting-text'))).toEqual([
      'Stuurt door tot en met 6 november 2026',
      'Stuurt door tot en met 20 oktober 2026',
    ]);
    expect(cells[0]!.find('code').attributes('translate')).toBe('no');
  });

  it('says it in English in English', () => {
    _setLocaleForTest('en');
    const wrapper = makeWrapper(siteProps({ previousSlugs: [OLD] }));

    expect(wrapper.find('h3').text()).toBe('Old addresses');
    expect(wrapper.find('nldd-text-cell').attributes('supporting-text')).toBe(
      'Redirects up to and including 6 November 2026',
    );
  });

  it('offers to change back to an old address, with a name that says to which', () => {
    const wrapper = makeWrapper(siteProps({ previousSlugs: [OLD] }));

    const button = wrapper.find('[data-testid="site-address-restore-oude-naam"]');
    expect(button.element.tagName.toLowerCase()).toBe('nldd-button');
    expect(button.attributes('text')).toBe('Zet terug');
    expect(button.attributes('accessible-label')).toBe(
      'Zet terug naar https://sites.plak.test/team-aurora/oude-naam/',
    );
    expect(button.attributes('appearance')).toBe('secondary');
    expect(button.attributes('size')).toBe('sm');
    expect(button.attributes('disabled')).toBeUndefined();
    // At the end of the row, in a cell no wider than the button.
    const cell = button.element.parentElement!;
    expect(cell.tagName.toLowerCase()).toBe('nldd-cell');
    expect(cell.getAttribute('width')).toBe('fit-content');
    expect(cell.previousElementSibling?.tagName.toLowerCase()).toBe('nldd-text-cell');
    expect(cell.nextElementSibling).toBeNull();
  });

  describe('for someone who may not change it', () => {
    it('says who may, in place of a form', () => {
      const wrapper = makeWrapper(siteProps({ canChange: false }));

      expect(wrapper.find('[data-testid="site-address-form"]').exists()).toBe(false);
      expect(wrapper.find('nldd-text-field').exists()).toBe(false);
      expect(wrapper.find('nldd-button').exists()).toBe(false);
      expect(wrapper.find('[data-testid="site-address-readonly"]').text()).toBe(
        'Alleen een beheerder van de site die ook lid is van de groep kan het adres wijzigen.',
      );
    });

    it('tells an admin of this site alone why it is not them', () => {
      const wrapper = makeWrapper(siteProps({ canChange: false, siteOnlyAdmin: true }));

      expect(wrapper.find('[data-testid="site-address-readonly"]').text()).toBe(
        'Je beheert deze site, maar je bent geen lid van de groep. Het adres wijzigen kan alleen een sitebeheerder die ook lid is van de groep.',
      );
    });

    it('keeps the address and the old ones in view, without a way to change back', () => {
      const wrapper = makeWrapper(siteProps({ canChange: false, previousSlugs: [OLD] }));

      expect(wrapper.find('[data-testid="site-address-current"]').text()).toContain(NEW_SITE);
      expect(wrapper.find('[data-testid="site-address-previous"] code').text()).toBe('team-aurora/oude-naam');
      expect(wrapper.find('[data-testid="site-address-restore-oude-naam"]').exists()).toBe(false);
      expect(wrapper.find('nldd-cell').exists()).toBe(false);
    });

    it('has no warning and no status line to listen to', () => {
      const wrapper = makeWrapper(siteProps({ canChange: false, isPublic: true }));

      expect(wrapper.find('nldd-banner').exists()).toBe(false);
      expect(wrapper.find('[role="status"]').exists()).toBe(false);
      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    });
  });
});

describe('AddressSection (site): the sheet', () => {
  it('offers a button on the page to change the address, and keeps the form in a sheet', () => {
    const wrapper = makeWrapper(siteProps({ previousSlugs: [OLD], isPublic: true }));

    const button = startButton(wrapper);
    expect(button.element.tagName.toLowerCase()).toBe('nldd-button');
    expect(button.attributes('text')).toBe('Adres wijzigen');
    expect(button.attributes('appearance')).toBe('secondary');
    expect(button.attributes('type')).toBe('button');
    expect(button.element.closest('nldd-sheet')).toBeNull();
    for (const testid of ['form', 'public', 'close', 'change']) {
      expect(wrapper.find(`[data-testid="site-address-${testid}"]`).element.closest('nldd-sheet')).not.toBeNull();
    }
    expect(statusLine(wrapper).element.closest('nldd-sheet')).toBeNull();
    // The current address and the old ones are on the page all the same.
    expect(wrapper.find('[data-testid="site-address-current"]').element.closest('nldd-sheet')).toBeNull();
    expect(wrapper.find('[data-testid="site-address-restore-oude-naam"]').element.closest('nldd-sheet')).toBeNull();
  });

  it('opens on the button only, named after what it is for', async () => {
    const spies = sheetSpies();
    try {
      const wrapper = makeWrapper(siteProps());
      await untilIdle();
      expect(spies.shown()).toBe(0);

      await openForm(wrapper);

      expect(spies.shown()).toBe(1);
      const sheet = wrapper.find('nldd-sheet');
      expect(sheet.attributes('placement')).toBe('right');
      expect(sheet.attributes('accessible-label')).toBe('Adres van de site wijzigen');
      expect(sheet.find('h2').text()).toBe('Adres van de site wijzigen');
      // nldd-page brings the scroller the sheet expects.
      expect(wrapper.find('nldd-sheet > nldd-page').exists()).toBe(true);
    } finally {
      spies.restore();
    }
  });

  it('lets the sheet move the focus to the field, which it does for an autofocus field', () => {
    const wrapper = makeWrapper(siteProps());

    expect(field(wrapper).attributes('autofocus')).toBeDefined();
    expect(wrapper.findAll('[autofocus]')).toHaveLength(1);
  });

  it('puts the sheet on the document root', () => {
    const wrapper = mountTeleported(siteProps());

    expect(document.body.querySelector('nldd-sheet')?.parentElement).toBe(document.body);
    wrapper.unmount();
    expect(document.body.querySelector('nldd-sheet')).toBeNull();
  });

  it('closes with a button in the title bar, set apart from the primary action', async () => {
    const spies = sheetSpies();
    try {
      const wrapper = await editing(siteProps());

      const close = wrapper.find('[data-testid="site-address-close"]');
      expect(close.attributes('slot')).toBe('end');
      expect(close.attributes('appearance')).toBe('neutral-transparent');
      expect(close.attributes('text')).toBe('Sluiten');
      expect(close.attributes('type')).toBe('button');
      expect(close.element.parentElement?.tagName.toLowerCase()).toBe('nldd-title');
      await close.trigger('click');
      await untilIdle();

      expect(spies.hidden()).toBe(1);
    } finally {
      spies.restore();
    }
  });

  it.each([
    ['its close button', (wrapper: Wrapper) => wrapper.find('[data-testid="site-address-close"]').trigger('click')],
    ['Escape, which closes the sheet itself', (wrapper: Wrapper) => wrapper.find('nldd-sheet').trigger('close')],
  ])('cancels on %s: forgets what was typed and its verdict, and gives the focus back to the button', async (_how, close) => {
    const wrapper = await editing(siteProps());
    type(wrapper, 'handboek');
    await submit(wrapper);
    vi.stubGlobal('fetch', problemFetch(409, { code: 'SLUG_EXISTS' }));
    await confirm(wrapper);
    await closeDialog(wrapper);
    expect(serverText(wrapper)).not.toBe('');
    const focus = vi.spyOn(HTMLElement.prototype, 'focus');

    await close(wrapper);
    await untilIdle();

    expect(field(wrapper).attributes('value')).toBe('');
    expect(field(wrapper).attributes('invalid')).toBeUndefined();
    expect(serverText(wrapper)).toBe('');
    expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="site-address-consequences"]').exists()).toBe(false);
    expect(focus.mock.contexts.at(-1)).toBe(startButton(wrapper).element);
    focus.mockRestore();
  });

  it('changes nothing on closing', async () => {
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);
    const wrapper = await editing(siteProps());
    type(wrapper, 'handboek');
    await untilIdle();

    await closeSheet(wrapper);

    expect(spy).not.toHaveBeenCalled();
    expect(dialog(wrapper).props('open')).toBe(false);
    expect(wrapper.emitted('renamed')).toBeUndefined();
  });

  it('puts the hints back once it is closed, as the list stays for the next time', async () => {
    const wrapper = mountTeleported(siteProps());
    await openForm(wrapper);
    // The list latches this once the field has been judged, and never lets go by itself.
    const list = document.body.querySelector('nldd-validation-list') as HTMLElement & { judging: boolean };
    list.judging = true;

    document.body.querySelector('nldd-sheet')!.dispatchEvent(new Event('close'));
    await untilIdle();

    expect(document.body.querySelector('nldd-validation-list')).toBe(list);
    expect(list.judging).toBe(false);
    wrapper.unmount();
  });
});

describe('AddressSection (site): the form', () => {
  it('gives a field named after the address, in an nldd-form-field with its label', async () => {
    const wrapper = await editing(siteProps());

    const input = field(wrapper);
    expect(input.element.tagName.toLowerCase()).toBe('nldd-text-field');
    expect(input.attributes('name')).toBe('site-address');
    expect(input.attributes('required')).toBeDefined();
    expect(input.attributes('autocomplete')).toBe('off');
    expect(input.attributes('no-spellcheck')).toBeDefined();
    expect(input.attributes('pattern')).toBe(SLUG_PATTERN);
    expect(input.attributes('value')).toBe('');
    expect(input.attributes('invalid')).toBeUndefined();
    // nldd-form-field links its label and validation list to direct children.
    expect(input.element.parentElement?.tagName.toLowerCase()).toBe('nldd-form-field');
    expect(input.element.parentElement?.getAttribute('label')).toBe('Nieuw adres');
  });

  it('never explains the field in a supporting label, which a screen reader does not read', async () => {
    const wrapper = await editing(siteProps());

    expect(wrapper.find('[supporting-label]').exists()).toBe(false);
  });

  it('judges the empty field with a requirement, which is no hint', async () => {
    const wrapper = await editing(siteProps());

    const required = wrapper.find('nldd-validation-item#site-address-required');
    expect(required.text()).toBe('Een adres is nodig');
    expect(required.attributes('required')).toBeDefined();
    expect(required.attributes('hint')).toBeUndefined();
  });

  it('keeps the rule of an address to itself until it is broken: the field already makes an address of what is typed', async () => {
    const wrapper = await editing(siteProps());

    const rule = wrapper.find('nldd-validation-item#site-address-rule');
    expect(rule.text()).toBe('Een adres begint en eindigt niet met een koppelteken');
    expect(rule.attributes('match')).toBe(SLUG_MATCH);
    expect(wrapper.find('nldd-validation-list').attributes('hint')).toBeUndefined();
    expect(wrapper.find('[hint]').exists()).toBe(false);
  });

  it('names only the hyphen at either end in its rule, as that is all the field can still hold wrong', () => {
    // Not anchored by the list, so it anchors itself.
    const rule = new RegExp(SLUG_MATCH, 'u');
    for (const value of ['mijn-', '-mijn', '-', '-mijn-']) {
      expect(rule.test(slugifyTyped(value))).toBe(false);
    }
    // Capitals, other characters and a 64th character never reach it.
    for (const value of ['Mijn', 'mijn_site', 'a'.repeat(64), 'mijn-site', 'mijn--site']) {
      expect(rule.test(slugifyTyped(value))).toBe(true);
    }
    // An empty value is the requirement's business, not this one's.
    expect(rule.test('')).toBe(true);
  });

  it('refuses the address it already has, with a rule of its own', async () => {
    const wrapper = await editing(siteProps());

    const differs = wrapper.find('nldd-validation-item#site-address-differs');
    expect(differs.text()).toBe('Het nieuwe adres verschilt van het huidige adres');
    expect(differs.attributes('hint')).toBeUndefined();
    // Not anchored by the list, so it anchors itself: any other value has it.
    const rule = new RegExp(differs.attributes('match')!, 'u');
    expect(rule.test('website')).toBe(false);
    expect(rule.test('websites')).toBe(true);
    expect(rule.test('mijn-website')).toBe(true);
    // An empty value is the requirement's business, not this one's.
    expect(rule.test('')).toBe(true);
  });

  it('keeps a verdict of the server for a last item, empty until there is one', async () => {
    const wrapper = await editing(siteProps());

    expect(serverText(wrapper)).toBe('');
    const items = wrapper.findAll('nldd-validation-item').map((item) => item.attributes('id'));
    expect(items).toEqual([
      'site-address-required',
      'site-address-rule',
      'site-address-differs',
      'site-address-server',
    ]);
  });

  it('hands the list the value the page keeps, which is not always the one in the field', async () => {
    const wrapper = await editing(siteProps());

    type(wrapper, 'handboek');
    await untilIdle();

    expect(wrapper.find('nldd-validation-list').attributes('value')).toBe('handboek');
  });

  it('turns what is typed into an address on the spot', async () => {
    const wrapper = await editing(siteProps());

    type(wrapper, 'Mijn Nieuwe Site!');
    await untilIdle();

    expect(field(wrapper).attributes('value')).toBe('mijn-nieuwe-site-');
  });

  it('lets a hyphen stand at the end, so the next letter can follow it', async () => {
    const wrapper = await editing(siteProps());

    type(wrapper, 'mijn ');
    await untilIdle();

    expect(field(wrapper).attributes('value')).toBe('mijn-');
  });

  it('takes the value of a native input event too', async () => {
    const wrapper = mountTeleported(siteProps());
    await openForm(wrapper);

    const input = document.body.querySelector('[data-testid="site-address"]') as HTMLElement & { value?: string };
    input.value = 'Handboek';
    input.dispatchEvent(new Event('input'));
    await untilIdle();

    // The element has a value of its own by now, so that is where the page puts the address.
    expect(input.value).toBe('handboek');
    wrapper.unmount();
  });

  describe('judging the field when it is left', () => {
    async function leave(wrapper: Wrapper): Promise<void> {
      await field(wrapper).trigger('change');
      await untilIdle();
    }

    function alertText(wrapper: Wrapper): string | null {
      const alert = wrapper.find('[role="alert"]');
      return alert.exists() ? alert.text() : null;
    }

    it('leaves an empty field alone, typed in or not', async () => {
      const wrapper = await editing(siteProps());

      await leave(wrapper);
      type(wrapper, 'x');
      type(wrapper, '');
      await leave(wrapper);

      expect(field(wrapper).attributes('invalid')).toBeUndefined();
      expect(alertText(wrapper)).toBeNull();
    });

    it('marks a value that is no address yet, and says which rule it breaks', async () => {
      const wrapper = await editing(siteProps());
      type(wrapper, 'mijn ');

      await leave(wrapper);

      expect(field(wrapper).attributes('invalid')).toBeDefined();
      expect(alertText(wrapper)).toBe('Een adres begint en eindigt niet met een koppelteken');
      expect(wrapper.find('[role="alert"]').element.closest('nldd-sheet')).not.toBeNull();
    });

    it.each([
      ['a hyphen at the start', ' mijn', '-mijn'],
      ['only a hyphen', '-', '-'],
    ])('marks %s with the same rule', async (_, typed, value) => {
      const wrapper = await editing(siteProps());
      type(wrapper, typed);

      await leave(wrapper);

      expect(field(wrapper).attributes('value')).toBe(value);
      expect(field(wrapper).attributes('invalid')).toBeDefined();
      expect(alertText(wrapper)).toBe('Een adres begint en eindigt niet met een koppelteken');
    });

    it.each([
      ['capitals', 'Mijn', 'mijn'],
      ['a 64th character', 'a'.repeat(64), 'a'.repeat(63)],
    ])('has no rule to break for %s: the field has made an address of them', async (_, typed, value) => {
      const wrapper = await editing(siteProps());
      type(wrapper, typed);

      await leave(wrapper);

      expect(field(wrapper).attributes('value')).toBe(value);
      expect(field(wrapper).attributes('invalid')).toBeUndefined();
      expect(alertText(wrapper)).toBeNull();
    });

    it('marks the address it already has, with the rule about that', async () => {
      const wrapper = await editing(siteProps());
      type(wrapper, 'website');

      await leave(wrapper);

      expect(field(wrapper).attributes('invalid')).toBeDefined();
      expect(alertText(wrapper)).toBe('Het nieuwe adres verschilt van het huidige adres');
    });

    it('says nothing of an address it can be changed to', async () => {
      const wrapper = await editing(siteProps());
      type(wrapper, 'handboek');

      await leave(wrapper);

      expect(field(wrapper).attributes('invalid')).toBeUndefined();
      expect(alertText(wrapper)).toBeNull();
    });

    it('lets the mark follow the value once judged, and says nothing aloud while it is typed', async () => {
      const wrapper = await editing(siteProps());
      type(wrapper, 'mijn ');
      await leave(wrapper);

      type(wrapper, 'mijn-site');
      await untilIdle();
      expect(field(wrapper).attributes('invalid')).toBeUndefined();
      expect(alertText(wrapper)).toBeNull();

      type(wrapper, 'website');
      await untilIdle();
      expect(field(wrapper).attributes('invalid')).toBeDefined();
      expect(alertText(wrapper)).toBeNull();
    });

    it('judges an empty field on submit, and only then', async () => {
      const wrapper = await editing(siteProps());

      await submit(wrapper);

      expect(field(wrapper).attributes('invalid')).toBeDefined();
      expect(dialog(wrapper).props('open')).toBe(false);
    });

    it('starts over when the sheet is opened again', async () => {
      const wrapper = await editing(siteProps());
      type(wrapper, 'mijn ');
      await leave(wrapper);

      await closeSheet(wrapper);
      await openForm(wrapper);

      expect(field(wrapper).attributes('invalid')).toBeUndefined();
      expect(alertText(wrapper)).toBeNull();
      type(wrapper, 'website');
      await untilIdle();
      expect(field(wrapper).attributes('invalid')).toBeUndefined();
    });

    it('says it in English in English', async () => {
      _setLocaleForTest('en');
      const wrapper = await editing(siteProps());
      type(wrapper, 'website');

      await leave(wrapper);

      expect(alertText(wrapper)).toBe('The new address differs from the current address');
    });

    it('names the hyphen rule in English in English', async () => {
      _setLocaleForTest('en');
      const wrapper = await editing(siteProps());
      type(wrapper, 'mijn ');

      await leave(wrapper);

      expect(alertText(wrapper)).toBe('An address does not start or end with a hyphen');
    });
  });

  it('has no help text while the field is empty', async () => {
    const wrapper = await editing(siteProps());

    expect(wrapper.find('nldd-form-field-help-text').exists()).toBe(false);
  });

  it('says in the help text what the address will be, from the first character on, as it is typed', async () => {
    const wrapper = await editing(siteProps());
    const help = () => wrapper.find('nldd-form-field-help-text');

    type(wrapper, 'Hand');
    await untilIdle();
    expect(help().text()).toBe('Het nieuwe adres wordt https://sites.plak.test/team-aurora/hand/');

    type(wrapper, 'handboek ');
    await untilIdle();
    expect(help().text()).toBe('Het nieuwe adres wordt https://sites.plak.test/team-aurora/handboek-/');
    expect(help().find('code').attributes('translate')).toBe('no');
    expect(help().element.parentElement?.tagName.toLowerCase()).toBe('nldd-form-field');

    type(wrapper, '');
    await untilIdle();
    expect(help().exists()).toBe(false);
  });

  it('has a status line from the start, empty, for a screen reader to watch', () => {
    const wrapper = makeWrapper(siteProps());

    const line = statusLine(wrapper);
    expect(line.exists()).toBe(true);
    expect(line.attributes('role')).toBe('status');
    expect(line.text()).toBe('');
  });

  it('puts one primary button under the form, which submits it', async () => {
    const wrapper = await editing(siteProps());

    const buttons = wrapper.findAll('nldd-form nldd-button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]!.attributes('text')).toBe('Adres wijzigen');
    expect(buttons[0]!.attributes('type')).toBe('submit');
    expect(buttons[0]!.attributes('appearance')).toBe('primary');
    expect(buttons[0]!.attributes('disabled')).toBeUndefined();
    expect(buttons[0]!.attributes('data-testid')).toBe('site-address-change');
    expect(buttons[0]!.element.parentElement?.tagName.toLowerCase()).toBe('nldd-form-actions');
    expect(wrapper.find('nldd-form-section').exists()).toBe(false);
  });

  describe('the warning for an address that has been shared', () => {
    it('stands above the field for a public site', async () => {
      const wrapper = await editing(siteProps({ isPublic: true }));

      const warning = wrapper.find('[data-testid="site-address-public"]');
      expect(warning.element.tagName.toLowerCase()).toBe('nldd-banner');
      expect(warning.attributes('variant')).toBe('warning');
      expect(warning.attributes('text')).toBe('Deze site is openbaar.');
      expect(warning.attributes('supporting-text')).toBe(
        'Mensen kunnen het adres hebben opgeslagen, in een document hebben gezet of hebben afgedrukt. Wijzig het adres alleen als het echt moet.',
      );
      const position = warning.element.compareDocumentPosition(field(wrapper).element);
      expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });

    it('is not there for a site that is not public', async () => {
      const wrapper = await editing(siteProps({ isPublic: false }));

      expect(wrapper.find('nldd-banner').exists()).toBe(false);
    });

    it('stands in the sheet, not on the page a member came to for something else', () => {
      const wrapper = makeWrapper(siteProps({ isPublic: true }));

      expect(wrapper.find('nldd-banner').element.closest('nldd-sheet')).not.toBeNull();
    });
  });
});

describe('AddressSection (site): what a change comes to', () => {
  function consequences(wrapper: Wrapper) {
    return wrapper.find('[data-testid="site-address-consequences"]');
  }

  /** The form, with an address in the field that it can be changed to. */
  async function typedNew(props = siteProps()): Promise<Wrapper> {
    const wrapper = await editing(props);
    type(wrapper, 'handboek');
    await untilIdle();
    return wrapper;
  }

  it('says nothing of it until the field holds an address it can be changed to', async () => {
    const wrapper = await editing(siteProps());
    expect(consequences(wrapper).exists()).toBe(false);

    for (const value of ['-handboek', 'website']) {
      type(wrapper, value);
      await untilIdle();
      expect(consequences(wrapper).exists()).toBe(false);
    }

    type(wrapper, 'handboek');
    await untilIdle();
    expect(consequences(wrapper).exists()).toBe(true);

    type(wrapper, '');
    await untilIdle();
    expect(consequences(wrapper).exists()).toBe(false);
  });

  it('is no live region, so a screen reader does not read it out on every key', async () => {
    const wrapper = await typedNew();

    const box = consequences(wrapper);
    expect(box.attributes('role')).toBeUndefined();
    expect(box.attributes('aria-live')).toBeUndefined();
    expect(box.element.closest('[role="status"], [role="alert"], [aria-live]')).toBeNull();
  });

  it('stands between the field and the button, as a list in rich text', async () => {
    const wrapper = await typedNew();

    const box = consequences(wrapper);
    expect(box.element.tagName.toLowerCase()).toBe('nldd-rich-text');
    expect(box.element.previousElementSibling?.tagName.toLowerCase()).toBe('nldd-form-field');
    expect(box.element.nextElementSibling?.tagName.toLowerCase()).toBe('nldd-form-actions');
    expect(box.findAll('p').map((p) => p.text())).toEqual([
      'Dit pas je zelf aan:',
      'Dit verandert er:',
    ]);
    expect(box.findAll('ul')).toHaveLength(2);
  });

  it('starts with what the member has to change themselves: the workflow, the site and the shared links', async () => {
    const wrapper = await typedNew();

    const items = consequences(wrapper).findAll('ul')[0]!.findAll('li');
    expect(items.map((item) => item.text())).toEqual([
      `Automatisch publiceren stopt meteen. Pas site: in je workflow en --site bij plak publish aan naar team-aurora/handboek, en zet er site-id: ${backend.data.sites[0]!.id} bij als dat er nog niet staat.`,
      'Staat het oude adres in je site, bijvoorbeeld in canonical, og:url, een sitemap of een vast pad? Publiceer de site dan vóór 7 november 2026 opnieuw met het nieuwe adres. Tot en met 7 november 2026 laden stijlen en scripts nog via het oude adres. Daarna niet meer.',
      'Heb je een geheime link gedeeld? Na 7 november 2026 vervang je het adres in de link door het nieuwe adres. Alles na ?key= blijft hetzelfde.',
    ]);
    expect(items[0]!.findAll('code').map((code) => code.text())).toEqual([
      'site:',
      '--site',
      'plak publish',
      'team-aurora/handboek',
      `site-id: ${backend.data.sites[0]!.id}`,
    ]);
    expect(items[1]!.findAll('code').map((code) => code.text())).toEqual(['canonical', 'og:url']);
    expect(items[2]!.findAll('code').map((code) => code.text())).toEqual(['?key=']);
  });

  it('follows the address as it is typed', async () => {
    const wrapper = await typedNew();

    type(wrapper, 'gids');
    await untilIdle();

    const codes = consequences(wrapper).findAll('ul')[0]!.findAll('li')[0]!.findAll('code');
    expect(codes[3]!.text()).toBe('team-aurora/gids');
  });

  it('goes on with what changes: the redirect, the end of it, the logins, the previews and the way back', async () => {
    const wrapper = await typedNew();

    const items = consequences(wrapper).findAll('ul')[1]!.findAll('li');
    expect(items.map((item) => item.text())).toEqual([
      'Het oude adres stuurt bezoekers door naar het nieuwe adres, tot en met 7 november 2026. Dat geldt alleen voor wie de site mag bekijken.',
      'Na die datum werkt het oude adres niet meer. Dan kan een andere site dit adres krijgen, en leidt een oude link naar andere inhoud.',
      'Bezoekers van een afgeschermde site moeten misschien opnieuw inloggen.',
      'Previews krijgen ook een nieuw adres. Links naar previews, bijvoorbeeld in een pull request, werken nog tot en met 7 november 2026.',
      'Tot en met 7 november 2026 kun je het oude adres terugzetten.',
    ]);
  });

  it('counts the days it is told to, not a number of its own', async () => {
    const wrapper = await typedNew(siteProps({ redirectDays: 14 }));

    expect(consequences(wrapper).text()).toContain('Tot en met 22 oktober 2026 kun je het oude adres terugzetten.');
  });
});

describe('AddressSection (site): changing the address', () => {
  it('opens no dialog for nothing, for the address it already has or for what is no address', async () => {
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);
    const wrapper = await editing(siteProps());

    await submit(wrapper);
    expect(dialog(wrapper).props('open')).toBe(false);

    type(wrapper, 'website');
    await submit(wrapper);
    expect(dialog(wrapper).props('open')).toBe(false);

    type(wrapper, '-handboek');
    await submit(wrapper);
    expect(dialog(wrapper).props('open')).toBe(false);

    expect(spy).not.toHaveBeenCalled();
  });

  it('asks for confirmation with the whole question, the short text and a confirmation that is not red', async () => {
    const wrapper = await editing(siteProps());

    type(wrapper, 'handboek');
    await submit(wrapper);

    const confirmation = dialog(wrapper);
    expect(confirmation.props('open')).toBe(true);
    expect(confirmation.props('title')).toBe(
      'Adres van site team-aurora/website wijzigen in team-aurora/handboek?',
    );
    expect(confirmation.props('text')).toBe(
      'Oude links sturen door tot en met 7 november 2026. Publiceren vanuit een workflow werkt pas weer nadat je het nieuwe adres instelt.',
    );
    expect(confirmation.props('keepLabel')).toBe('Behoud huidig adres');
    expect(confirmation.props('confirmLabel')).toBe('Wijzig adres');
    expect(confirmation.props('confirmVariant')).toBe('secondary');
    expect(confirmation.props('busy')).toBe(false);
  });

  it('reads the day again when a change is asked for, because a page can stay open past midnight', async () => {
    const wrapper = await editing(siteProps());
    // Midnight in Amsterdam has passed since the page was opened: it is the 9th there.
    vi.setSystemTime(new Date('2026-10-08T22:30:00Z'));
    type(wrapper, 'handboek');

    await submit(wrapper);

    expect(dialog(wrapper).props('text')).toContain('tot en met 8 november 2026');
    expect(wrapper.find('[data-testid="site-address-consequences"]').text()).toContain(
      'Tot en met 8 november 2026 kun je het oude adres terugzetten.',
    );
  });

  it('asks for no sentence to be typed: undoing it is possible, so there is nothing to type', async () => {
    const wrapper = await editing(siteProps());

    type(wrapper, 'handboek');
    await submit(wrapper);

    expect(dialog(wrapper).props('confirmPhrase')).toBeUndefined();
    expect(wrapper.find('[data-testid="confirm-phrase"]').exists()).toBe(false);
  });

  it('puts the safe way out first, and changes nothing with it', async () => {
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);
    const wrapper = await editing(siteProps());
    type(wrapper, 'handboek');
    await submit(wrapper);
    const actions = wrapper.findAll('nldd-modal-dialog nldd-button');
    expect(actions[0]!.attributes('data-testid')).toBe('confirm-cancel');
    expect(actions[0]!.attributes('appearance')).toBe('primary');

    await wrapper.find('[data-testid="confirm-cancel"]').trigger('click');
    await untilIdle();

    expect(dialog(wrapper).props('open')).toBe(false);
    expect(spy).not.toHaveBeenCalled();
    expect(backend.data.sites[0]!.slug).toBe('website');
    expect(wrapper.emitted('renamed')).toBeUndefined();
    // What was typed stays, to be changed or sent.
    expect(field(wrapper).attributes('value')).toBe('handboek');
  });

  it('lets Escape close the dialog the same way', async () => {
    const wrapper = await editing(siteProps());
    type(wrapper, 'handboek');
    await submit(wrapper);

    await closeDialog(wrapper);

    expect(dialog(wrapper).props('open')).toBe(false);
    expect(backend.data.sites[0]!.slug).toBe('website');
  });

  describe('when it works', () => {
    async function changed(wrapper: Wrapper, slug = 'handboek'): Promise<void> {
      type(wrapper, slug);
      await submit(wrapper);
      await confirm(wrapper);
    }

    it('sends the new address through the real client and passes the site on', async () => {
      const spy = vi.fn(backend.fetch);
      vi.stubGlobal('fetch', spy);
      const wrapper = await editing(siteProps());

      await changed(wrapper);

      const [path, init] = spy.mock.calls[0]!;
      expect(path).toBe('/-/api/v1/sites/team-aurora/website/slug');
      expect(JSON.parse(String(init?.body))).toEqual({ slug: 'handboek' });
      expect(backend.data.sites[0]!.slug).toBe('handboek');
      expect(wrapper.emitted('renamed')).toHaveLength(1);
      expect(wrapper.emitted('renamed')![0]![0]).toMatchObject({
        slug: 'handboek',
        previousSlugs: [{ slug: 'website' }],
      });
    });

    it('closes the dialog, then the sheet once the dialog is gone, and shows no notification', async () => {
      const spies = sheetSpies();
      try {
        const wrapper = await editing(siteProps());

        await changed(wrapper);
        expect(dialog(wrapper).props('open')).toBe(false);
        expect(dialog(wrapper).props('busy')).toBe(false);
        expect(spies.hidden()).toBe(0);

        await closeDialog(wrapper);
        expect(spies.hidden()).toBe(1);

        await closeSheet(wrapper);
        expect(field(wrapper).attributes('value')).toBe('');
        expect(wrapper.find('nldd-notification').exists()).toBe(false);
        expect(wrapper.find('[role="alert"]').exists()).toBe(false);
      } finally {
        spies.restore();
      }
    });

    it('opens an empty sheet the next time', async () => {
      const wrapper = await editing(siteProps());
      await changed(wrapper);
      await closeOverlays(wrapper);

      await openForm(wrapper);

      expect(field(wrapper).attributes('value')).toBe('');
      expect(wrapper.find('[data-testid="site-address-consequences"]').exists()).toBe(false);
    });

    it('says what happened in the status line only once the dialog and the sheet are gone, because behind them nothing is heard', async () => {
      const wrapper = await editing(siteProps());

      await changed(wrapper);
      expect(statusLine(wrapper).text()).toBe('');
      await closeDialog(wrapper);
      // The sheet is still in front of the page.
      expect(statusLine(wrapper).text()).toBe('');

      await closeSheet(wrapper);
      expect(statusLine(wrapper).text()).toBe(
        'Het adres is gewijzigd. team-aurora/website stuurt tot en met 7 november 2026 door naar team-aurora/handboek. Gebruik je automatisch publiceren of plak publish? Pas het adres daar nu aan.',
      );
    });

    it('puts the addresses and the command in code that a browser leaves untranslated', async () => {
      const wrapper = await editing(siteProps());
      await changed(wrapper);

      await closeOverlays(wrapper);

      const codes = statusLine(wrapper).findAll('code');
      expect(codes.map((code) => code.text())).toEqual([
        'team-aurora/website',
        'team-aurora/handboek',
        'plak publish',
      ]);
      expect(codes.every((code) => code.attributes('translate') === 'no')).toBe(true);
    });

    it('puts the focus on the button that asks for the form, which stands where the form was', async () => {
      const wrapper = await editing(siteProps());
      await changed(wrapper);
      const focus = vi.spyOn(HTMLElement.prototype, 'focus');

      await closeOverlays(wrapper);

      expect(focus).toHaveBeenCalledTimes(1);
      expect(focus.mock.contexts[0]).toBe(startButton(wrapper).element);
      focus.mockRestore();
    });

    it('reads the last day from the answer, which knows better than the page what day it is', async () => {
      const wrapper = await editing(siteProps());
      type(wrapper, 'handboek');
      await submit(wrapper);
      // The dialog stood open past midnight.
      vi.setSystemTime(new Date('2026-10-09T10:00:00Z'));
      await confirm(wrapper);

      await closeOverlays(wrapper);

      expect(statusLine(wrapper).text()).toContain('stuurt tot en met 8 november 2026 door');
    });

    it('falls back on its own count of the days when the answer does not name the old address', async () => {
      vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
        const response = await backend.fetch(input, init);
        if (init?.method !== 'PUT') return response;
        const site = (await response.json()) as Site;
        return new Response(JSON.stringify({ ...site, previousSlugs: [] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        });
      });
      const wrapper = await editing(siteProps());
      await changed(wrapper);

      await closeOverlays(wrapper);

      expect(statusLine(wrapper).text()).toContain('stuurt tot en met 7 november 2026 door');
    });

    it('says it in English in English, with the date in English', async () => {
      _setLocaleForTest('en');
      const wrapper = await editing(siteProps());
      await changed(wrapper);

      await closeOverlays(wrapper);

      expect(statusLine(wrapper).text()).toBe(
        'The address has been changed. team-aurora/website redirects to team-aurora/handboek up to and including 7 November 2026. Do you publish automatically or with plak publish? Change the address there now.',
      );
    });

    it('takes the line back when the field is edited again', async () => {
      const wrapper = await editing(siteProps());
      await changed(wrapper);
      await closeOverlays(wrapper);
      expect(statusLine(wrapper).text()).not.toBe('');
      await openForm(wrapper);
      expect(statusLine(wrapper).text()).not.toBe('');

      type(wrapper, 'x');
      await untilIdle();

      expect(statusLine(wrapper).text()).toBe('');
    });

    it('goes on from the new address when the page hands it down', async () => {
      const wrapper = await editing(siteProps());
      await changed(wrapper);
      const updated = wrapper.emitted('renamed')![0]![0] as Site;

      await wrapper.setProps({ site: updated.slug, previousSlugs: updated.previousSlugs });

      expect(wrapper.find('[data-testid="site-address-current"]').text()).toContain(
        'https://sites.plak.test/team-aurora/handboek/',
      );
      const cell = wrapper.find('[data-testid="site-address-previous"] nldd-text-cell');
      expect(cell.find('code').text()).toBe('team-aurora/website');
      expect(cell.attributes('supporting-text')).toBe('Stuurt door tot en met 7 november 2026');
      await openForm(wrapper);
      const differs = wrapper.find('nldd-validation-item#site-address-differs');
      expect(new RegExp(differs.attributes('match')!, 'u').test('handboek')).toBe(false);
      expect(new RegExp(differs.attributes('match')!, 'u').test('website')).toBe(true);
    });

    it('still passes the site on when the form is gone by the time the answer arrives', async () => {
      let answer: (response: Response) => void = () => {};
      vi.stubGlobal('fetch', () => new Promise<Response>((resolve) => (answer = resolve)));
      const wrapper = await editing(siteProps());
      type(wrapper, 'handboek');
      await submit(wrapper);
      await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
      // The role was taken away in the meantime.
      await wrapper.setProps({ canChange: false });

      answer(
        new Response(JSON.stringify({ ...backend.data.sites[0]!, slug: 'handboek', previousSlugs: [{ ...OLD, slug: 'website' }] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      );
      await untilIdle();

      expect(wrapper.emitted('renamed')).toHaveLength(1);
    });
  });

  describe('changing back', () => {
    async function renamedBefore(): Promise<Wrapper> {
      const before = await toon.setSiteSlug('team-aurora', 'website', 'handboek');
      return makeWrapper(siteProps({ site: 'handboek', previousSlugs: before.previousSlugs }));
    }

    it('asks the same question about the old address, and confirms it the same way', async () => {
      const wrapper = await renamedBefore();

      await wrapper.find('[data-testid="site-address-restore-website"]').trigger('click');
      await untilIdle();

      expect(dialog(wrapper).props('open')).toBe(true);
      expect(dialog(wrapper).props('title')).toBe(
        'Adres van site team-aurora/handboek wijzigen in team-aurora/website?',
      );
      expect(dialog(wrapper).props('confirmPhrase')).toBeUndefined();

      await confirm(wrapper);
      await closeDialog(wrapper);

      expect(backend.data.sites[0]!.slug).toBe('website');
      expect(wrapper.emitted('renamed')![0]![0]).toMatchObject({ slug: 'website' });
      expect(statusLine(wrapper).text()).toContain(
        'team-aurora/handboek stuurt tot en met 7 november 2026 door naar team-aurora/website.',
      );
    });

    it('does not mix up what was typed with the address it changes back to', async () => {
      const wrapper = await renamedBefore();
      await openForm(wrapper);
      type(wrapper, 'iets-anders');

      await wrapper.find('[data-testid="site-address-restore-website"]').trigger('click');
      await untilIdle();
      await confirm(wrapper);

      expect(backend.data.sites[0]!.slug).toBe('website');
      // The typed text is gone with the sheet: it was about an address that is now the old one.
      await closeOverlays(wrapper);
      expect(field(wrapper).attributes('value')).toBe('');
    });

    it('says a refusal in a notification when it comes from the page, as there is no field to say it at', async () => {
      const wrapper = await renamedBefore();
      await wrapper.find('[data-testid="site-address-restore-website"]').trigger('click');
      await untilIdle();
      vi.stubGlobal('fetch', problemFetch(409, { title: 'Conflict', detail: 'Not shown.', code: 'SLUG_EXISTS' }));

      await confirm(wrapper);
      await closeDialog(wrapper);

      const notice = wrapper.find('nldd-notification[text="Adres niet gewijzigd"]');
      expect(notice.attributes('variant')).toBe('critical');
      expect(notice.attributes('supporting-text')).toBe(
        'Dit adres is al in gebruik of was kort geleden van een andere site. Kies een ander adres.',
      );
      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
      expect(field(wrapper).attributes('invalid')).toBeUndefined();
    });
  });

  describe('while the change is on its way', () => {
    it('marks the confirmation as busy and keeps the dialog open, however it is closed', async () => {
      let answer: (response: Response) => void = () => {};
      const spy = vi.fn(() => new Promise<Response>((resolve) => (answer = resolve)));
      vi.stubGlobal('fetch', spy);
      const wrapper = await editing(siteProps());
      type(wrapper, 'handboek');
      await submit(wrapper);

      await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
      await wrapper.find('[data-testid="confirm-continue"]').trigger('click');
      await wrapper.find('[data-testid="confirm-cancel"]').trigger('click');
      await untilIdle();

      expect(spy).toHaveBeenCalledTimes(1);
      expect(dialog(wrapper).props('busy')).toBe(true);
      expect(dialog(wrapper).props('open')).toBe(true);
      expect(wrapper.find('[data-testid="confirm-continue"]').attributes('loading')).toBeDefined();
      expect(wrapper.find('[data-testid="confirm-continue"]').attributes('disabled')).toBeUndefined();

      answer(
        new Response(JSON.stringify({ ...backend.data.sites[0]!, slug: 'handboek', previousSlugs: [] }), {
          status: 200,
          headers: { 'content-type': 'application/json' },
        }),
      );
      await untilIdle();
      expect(dialog(wrapper).props('busy')).toBe(false);
      expect(dialog(wrapper).props('open')).toBe(false);
    });
  });
});

describe('AddressSection (site): when it does not work', () => {
  async function refused(
    wrapper: Wrapper,
    status: number,
    body: Record<string, unknown>,
    typed = 'handboek',
  ): Promise<void> {
    type(wrapper, typed);
    await submit(wrapper);
    vi.stubGlobal('fetch', problemFetch(status, body));
    await confirm(wrapper);
  }

  describe.each([
    [
      422,
      'SLUG_INVALID',
      'Dit adres kan niet worden gebruikt. Kies een ander adres.',
    ],
    [
      409,
      'SLUG_EXISTS',
      'Dit adres is al in gebruik of was kort geleden van een andere site. Kies een ander adres.',
    ],
    [
      409,
      'TOO_MANY_PREVIOUS_SLUGS',
      'Deze site heeft al vijf oude adressen die nog doorsturen. Zet een oud adres terug, of wacht tot er een is vrijgekomen.',
    ],
  ])('when the server refuses with %i %s', (status, code, text) => {
    const body = { title: 'Refused', detail: 'The server wording, not shown.', code };

    it('closes the dialog first, and does not say anything while the page behind it cannot be heard', async () => {
      const wrapper = await editing(siteProps());

      await refused(wrapper, status, body);

      expect(dialog(wrapper).props('open')).toBe(false);
      expect(field(wrapper).attributes('invalid')).toBeUndefined();
      expect(serverText(wrapper)).toBe('');
      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    });

    it('then says what is wrong at the field, in the words of the interface', async () => {
      const wrapper = await editing(siteProps());
      await refused(wrapper, status, body);

      await closeDialog(wrapper);

      const input = field(wrapper);
      expect(input.attributes('invalid')).toBeDefined();
      expect(input.attributes('unmet')).toBe('site-address-server');
      expect(serverText(wrapper)).toBe(text);
      expect(wrapper.html()).not.toContain('The server wording');
      expect(wrapper.find('nldd-notification').exists()).toBe(false);
      expect(wrapper.emitted('renamed')).toBeUndefined();
      expect(statusLine(wrapper).text()).toBe('');
    });

    it('moves the focus to the field, which still holds what was typed', async () => {
      const wrapper = await editing(siteProps());
      await refused(wrapper, status, body);
      const focus = vi.spyOn(HTMLElement.prototype, 'focus');

      await closeDialog(wrapper);

      expect(focus).toHaveBeenCalledTimes(1);
      expect(focus.mock.contexts[0]).toBe(field(wrapper).element);
      expect(field(wrapper).attributes('value')).toBe('handboek');
      focus.mockRestore();
    });

    it('says the verdict aloud too, which is all there is to hear when Enter was pressed in the field', async () => {
      const wrapper = await editing(siteProps());
      await refused(wrapper, status, body);

      await closeDialog(wrapper);

      const alert = wrapper.find('[role="alert"]');
      expect(alert.text()).toBe(text);
      expect(alert.classes()).toContain('visually-hidden');
      expect(alert.attributes('data-testid')).toBe('site-address-alert');
    });

    it('takes the verdict back as soon as the field is edited', async () => {
      const wrapper = await editing(siteProps());
      await refused(wrapper, status, body);
      await closeDialog(wrapper);

      type(wrapper, 'handboek-2');
      await untilIdle();

      expect(field(wrapper).attributes('invalid')).toBeUndefined();
      expect(field(wrapper).attributes('unmet')).toBeUndefined();
      expect(serverText(wrapper)).toBe('');
      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
    });

    it('lets a corrected address through afterwards', async () => {
      const wrapper = await editing(siteProps());
      await refused(wrapper, status, body);
      await closeDialog(wrapper);
      vi.stubGlobal('fetch', backend.fetch);

      type(wrapper, 'gids');
      await submit(wrapper);
      await confirm(wrapper);

      expect(backend.data.sites[0]!.slug).toBe('gids');
      expect(wrapper.emitted('renamed')).toHaveLength(1);
    });
  });

  it('tells the member in English at the field', async () => {
    _setLocaleForTest('en');
    const wrapper = await editing(siteProps());
    await refused(wrapper, 409, { title: 'Conflict', code: 'SLUG_EXISTS' });

    await closeDialog(wrapper);

    expect(serverText(wrapper)).toBe(
      'This address is already in use, or recently belonged to another site. Choose another address.',
    );
  });

  describe.each([
    [429, { title: 'Te veel verzoeken', detail: 'Probeer het over een minuut opnieuw.', code: 'TOO_MANY_CREATIONS' }, 'Probeer het over een minuut opnieuw.'],
    [403, { title: 'Geen toegang', detail: 'Hiervoor heb je minimaal de rol beheerder nodig.', code: 'INSUFFICIENT_ROLE' }, 'Hiervoor heb je minimaal de rol beheerder nodig.'],
    [404, { title: 'Onbekende site', code: 'UNKNOWN_SITE' }, 'Onbekende site'],
  ])('when it is refused with %i for another reason than the address', (status, body, detail) => {
    it('reports it as a critical notification once the dialog is gone, and leaves the field clean', async () => {
      const wrapper = await editing(siteProps());
      await refused(wrapper, status, body);
      expect(wrapper.find('nldd-notification').exists()).toBe(false);

      await closeDialog(wrapper);

      const notice = wrapper.find('nldd-notification[text="Adres niet gewijzigd"]');
      expect(notice.attributes('variant')).toBe('critical');
      expect(notice.attributes('supporting-text')).toBe(detail);
      expect(field(wrapper).attributes('invalid')).toBeUndefined();
      expect(wrapper.find('[role="alert"]').exists()).toBe(false);
      expect(wrapper.emitted('renamed')).toBeUndefined();
      expect(field(wrapper).attributes('value')).toBe('handboek');
    });
  });

  it('reports a refusal by the role the same way, from the real mock', async () => {
    const wrapper = await editing(siteProps());
    type(wrapper, 'handboek');
    await submit(wrapper);
    backend.data.loggedInMemberId = 'lid-3';

    await confirm(wrapper);
    await closeDialog(wrapper);

    const notice = wrapper.find('nldd-notification[text="Adres niet gewijzigd"]');
    expect(notice.attributes('supporting-text')).toBe(
      'Hiervoor heb je minimaal de rol beheerder nodig.',
    );
  });

  it('reports a failure that is not a refusal with a sentence of its own', async () => {
    const wrapper = await editing(siteProps());
    type(wrapper, 'handboek');
    await submit(wrapper);
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));

    await confirm(wrapper);
    await closeDialog(wrapper);

    expect(wrapper.find('nldd-notification[text="Adres niet gewijzigd"]').attributes('supporting-text')).toBe(
      'Wijzigen is niet gelukt.',
    );
  });

  it('reports the title of a problem without a detail', async () => {
    const wrapper = await editing(siteProps());
    type(wrapper, 'handboek');
    await submit(wrapper);
    vi.stubGlobal('fetch', serverErrorFetch());

    await confirm(wrapper);
    await closeDialog(wrapper);

    expect(wrapper.find('nldd-notification[text="Adres niet gewijzigd"]').attributes('supporting-text')).toBe(
      'Serverfout',
    );
  });

  it('can change the address again after a failure', async () => {
    const wrapper = await editing(siteProps());
    type(wrapper, 'handboek');
    await submit(wrapper);
    vi.stubGlobal('fetch', serverErrorFetch());
    await confirm(wrapper);
    await closeDialog(wrapper);
    vi.stubGlobal('fetch', backend.fetch);

    await submit(wrapper);
    await confirm(wrapper);

    expect(backend.data.sites[0]!.slug).toBe('handboek');
  });

  it('shows nothing of an earlier refusal once the next request is under way', async () => {
    const wrapper = await editing(siteProps());
    await refused(wrapper, 409, { code: 'SLUG_EXISTS' });
    await closeDialog(wrapper);
    expect(serverText(wrapper)).not.toBe('');

    await submit(wrapper);

    expect(serverText(wrapper)).toBe('');
    expect(field(wrapper).attributes('invalid')).toBeUndefined();
  });
});

describe('AddressSection (site): an address another site in the group gave up', () => {
  /** Two other sites in the group; the second gave up `gids`, which redirects to it until the 6th of November. */
  function siblings(title = 'Handboek'): Site[] {
    const other = backend.data.sites[0]!;
    const unrelated = { ...other, id: 'derde-id', slug: 'derde', title: 'Derde', previousSlugs: [OLDER] };
    const owner = {
      ...other,
      id: 'tweede-id',
      slug: 'handboek',
      title,
      previousSlugs: [{ slug: 'gids', redirectsUntil: OLD.redirectsUntil }],
    };
    backend.data.sites.push(unrelated, owner);
    return [unrelated, owner];
  }

  async function typedGids(props: Props): Promise<Wrapper> {
    const wrapper = await editing(props);
    type(wrapper, 'gids');
    await submit(wrapper);
    return wrapper;
  }

  describe('for an admin of the group', () => {
    it('asks first whether to take it from that site, and says what that ends', async () => {
      const wrapper = await typedGids(siteProps({ siblings: siblings(), canRelease: true }));

      const confirmation = dialog(wrapper);
      expect(confirmation.props('open')).toBe(true);
      expect(confirmation.props('title')).toBe(
        'Adres van site team-aurora/website wijzigen in team-aurora/gids?',
      );
      expect(confirmation.props('text')).toBe(
        'Dit adres was tot kort geleden van Handboek en stuurt tot en met 6 november 2026 door naar die site. Gebruik je het toch, dan stopt die doorsturing nu en komen oude links van Handboek bij deze site uit. Oude links sturen door tot en met 7 november 2026. Publiceren vanuit een workflow werkt pas weer nadat je het nieuwe adres instelt.',
      );
      expect(confirmation.props('confirmLabel')).toBe('Toch gebruiken');
      expect(confirmation.props('confirmVariant')).toBe('secondary');
    });

    it('asks it in English in English', async () => {
      _setLocaleForTest('en');
      const wrapper = await typedGids(siteProps({ siblings: siblings(), canRelease: true }));

      expect(dialog(wrapper).props('text')).toBe(
        'This address recently belonged to Handboek and redirects to that site up to and including 6 November 2026. If you use it anyway, that redirect stops now and old links to Handboek lead to this site. Old links redirect up to and including 7 November 2026. Publishing from a workflow only works again once you set the new address.',
      );
      expect(dialog(wrapper).props('confirmLabel')).toBe('Use anyway');
    });

    it('asks to release it on confirmation, which ends the redirect of the other site', async () => {
      const spy = vi.fn(backend.fetch);
      vi.stubGlobal('fetch', spy);
      const wrapper = await typedGids(siteProps({ siblings: siblings(), canRelease: true }));

      await confirm(wrapper);

      const [path, init] = spy.mock.calls[0]!;
      expect(path).toBe('/-/api/v1/sites/team-aurora/website/slug');
      expect(JSON.parse(String(init?.body))).toEqual({ slug: 'gids', release: true });
      expect(backend.data.sites[0]!.slug).toBe('gids');
      expect(backend.data.sites[2]!.previousSlugs).toEqual([]);
      expect(wrapper.emitted('renamed')![0]![0]).toMatchObject({ slug: 'gids' });
    });

    it('puts the name of the other site in as text, never as markup or as a placeholder', async () => {
      const wrapper = await typedGids(
        siteProps({ siblings: siblings('<b>Handboek</b> {date}'), canRelease: true }),
      );

      expect(dialog(wrapper).props('text')).toContain(
        'Dit adres was tot kort geleden van <b>Handboek</b> {date} en stuurt tot en met 6 november 2026 door',
      );
      expect(wrapper.find('nldd-modal-dialog b').exists()).toBe(false);
    });

    it('asks the plain question again for the next address, and changes back without releasing anything', async () => {
      const spy = vi.fn(backend.fetch);
      vi.stubGlobal('fetch', spy);
      backend.data.sites[0]!.previousSlugs = [OLD];
      const wrapper = await typedGids(
        siteProps({ previousSlugs: [OLD], siblings: siblings(), canRelease: true }),
      );
      await wrapper.find('[data-testid="confirm-cancel"]').trigger('click');
      await untilIdle();

      await wrapper.find('[data-testid="site-address-restore-oude-naam"]').trigger('click');
      await untilIdle();

      expect(dialog(wrapper).props('text')).toBe(
        'Oude links sturen door tot en met 7 november 2026. Publiceren vanuit een workflow werkt pas weer nadat je het nieuwe adres instelt.',
      );
      expect(dialog(wrapper).props('confirmLabel')).toBe('Wijzig adres');
      await confirm(wrapper);
      expect(JSON.parse(String(spy.mock.calls[0]![1]?.body))).toEqual({ slug: 'oude-naam' });
    });

    it('says at the field that it is taken when another member was first after all', async () => {
      const wrapper = await typedGids(siteProps({ siblings: siblings(), canRelease: true }));
      vi.stubGlobal('fetch', problemFetch(409, { title: 'Conflict', code: 'SLUG_EXISTS' }));

      await confirm(wrapper);
      await closeDialog(wrapper);

      expect(serverText(wrapper)).toBe(
        'Dit adres is al in gebruik of was kort geleden van een andere site. Kies een ander adres.',
      );
    });

    it('reports a refusal by the role as a critical notification, from the real mock', async () => {
      // lid-4 is admin of this site and a reader in the group: the page thought otherwise.
      backend.data.loggedInMemberId = 'lid-4';
      const wrapper = await typedGids(siteProps({ siblings: siblings(), canRelease: true }));

      await confirm(wrapper);
      await closeDialog(wrapper);

      expect(
        wrapper.find('nldd-notification[text="Adres niet gewijzigd"]').attributes('supporting-text'),
      ).toBe('Hiervoor heb je minimaal de rol beheerder nodig.');
      expect(backend.data.sites[0]!.slug).toBe('website');
      expect(backend.data.sites[2]!.previousSlugs).toHaveLength(1);
    });
  });

  describe('for anyone else who may change the address', () => {
    it('says at the field that it stays reserved, without asking anything or sending anything', async () => {
      const spy = vi.fn(backend.fetch);
      vi.stubGlobal('fetch', spy);
      const wrapper = makeWrapper(siteProps({ siblings: siblings(), canRelease: false }));
      await openForm(wrapper);
      type(wrapper, 'gids');
      const focus = vi.spyOn(HTMLElement.prototype, 'focus');

      await submit(wrapper);

      const text =
        'Dit adres was tot kort geleden van Handboek en blijft tot en met 6 november 2026 voor die site gereserveerd. Alleen een beheerder van de groep kan het vrijgeven.';
      expect(spy).not.toHaveBeenCalled();
      expect(dialog(wrapper).props('open')).toBe(false);
      expect(serverText(wrapper)).toBe(text);
      expect(field(wrapper).attributes('invalid')).toBeDefined();
      expect(field(wrapper).attributes('unmet')).toBe('site-address-server');
      expect(wrapper.find('[role="alert"]').text()).toBe(text);
      expect(focus.mock.contexts).toEqual([field(wrapper).element]);
      focus.mockRestore();
    });

    it('says it in English in English, the name of the site as text', async () => {
      _setLocaleForTest('en');
      const wrapper = await typedGids(siteProps({ siblings: siblings('<i>Gids</i>') }));

      expect(serverText(wrapper)).toBe(
        'This address recently belonged to <i>Gids</i> and stays reserved for that site up to and including 6 November 2026. Only an administrator of the group can release it.',
      );
      expect(wrapper.find('nldd-validation-item i').exists()).toBe(false);
    });

    it('lets any other address through as before', async () => {
      const wrapper = await editing(siteProps({ siblings: siblings() }));
      type(wrapper, 'nieuw');

      await submit(wrapper);

      expect(dialog(wrapper).props('open')).toBe(true);
      expect(dialog(wrapper).props('confirmLabel')).toBe('Wijzig adres');
    });
  });

  it('never comes up for the address of a group', async () => {
    const spy = vi.fn(backend.fetch);
    vi.stubGlobal('fetch', spy);
    const wrapper = await editing(groupProps({ siblings: siblings(), canRelease: true }));
    type(wrapper, 'gids', 'group');

    await submit(wrapper, 'group');

    expect(dialog(wrapper).props('text')).not.toContain('Handboek');
    expect(dialog(wrapper).props('confirmLabel')).toBe('Wijzig adres');
    await confirm(wrapper);
    expect(JSON.parse(String(spy.mock.calls[0]![1]?.body))).toEqual({ slug: 'gids' });
  });
});

describe('AddressSection (group)', () => {
  it('puts the section under its own heading, named after the group', async () => {
    const wrapper = await editing(groupProps());

    expect(wrapper.find('h2').text()).toBe('Adres van de groep');
    expect(wrapper.find('section').attributes('aria-labelledby')).toBe('heading-group-address');
    expect(field(wrapper, 'group').attributes('name')).toBe('group-address');
    expect(wrapper.find('nldd-validation-item#group-address-required').exists()).toBe(true);
    expect(wrapper.find('[data-testid="group-address-change"]').attributes('text')).toBe('Adres wijzigen');
  });

  it('says what the address of the group is and where it shows, with a site as the example', () => {
    const wrapper = makeWrapper(groupProps());

    const current = wrapper.find('[data-testid="group-address-current"]');
    expect(current.text()).toBe(
      `Het adres van deze groep is team-aurora. Het staat in het adres van elke site, bijvoorbeeld ${NEW_SITE}.`,
    );
    expect(current.find('code').text()).toBe('team-aurora');
    const link = current.find('nldd-link');
    expect(link.text()).toBe(NEW_SITE);
    expect(link.attributes('href')).toBe(NEW_SITE);
  });

  it('says only where it shows when the group has no site to use as an example', () => {
    const wrapper = makeWrapper(groupProps({ sites: [] }));

    expect(wrapper.find('[data-testid="group-address-current"]').text()).toBe(
      'Het adres van deze groep is team-aurora. Het staat in het adres van elke site in deze groep.',
    );
  });

  it('lists an old address of the group as the group, without the site behind it', () => {
    const wrapper = makeWrapper(groupProps({ previousSlugs: [OLD] }));

    expect(wrapper.find('h3').attributes('id')).toBe('heading-group-address-previous');
    const cell = wrapper.find('[data-testid="group-address-previous"] nldd-text-cell');
    expect(cell.find('code').text()).toBe('oude-naam');
    expect(cell.attributes('supporting-text')).toBe('Stuurt door tot en met 6 november 2026');
    expect(
      wrapper.find('[data-testid="group-address-restore-oude-naam"]').attributes('accessible-label'),
    ).toBe('Zet terug naar oude-naam');
  });

  it('says who may change it, in place of a form', () => {
    const wrapper = makeWrapper(groupProps({ canChange: false }));

    expect(wrapper.find('[data-testid="group-address-form"]').exists()).toBe(false);
    expect(startButton(wrapper, 'group').exists()).toBe(false);
    expect(wrapper.find('[data-testid="group-address-readonly"]').text()).toBe(
      'Alleen een beheerder van de groep kan het adres wijzigen.',
    );
  });

  it('warns when the group holds a public site, in the words about a group', async () => {
    const wrapper = await editing(groupProps({ isPublic: true }));

    const warning = wrapper.find('[data-testid="group-address-public"]');
    expect(warning.attributes('variant')).toBe('warning');
    expect(warning.attributes('text')).toBe('In deze groep staan openbare sites.');
    expect(warning.attributes('supporting-text')).toBe(
      'Hun adressen kunnen in documenten, e-mails of op papier staan. Wijzig het adres alleen als het echt moet.',
    );
  });

  it('shows the new address of the group as what it is, the slug, and not as a page that does not exist', async () => {
    const wrapper = await editing(groupProps());

    type(wrapper, 'aurora', 'group');
    await untilIdle();

    const help = wrapper.find('nldd-form-field-help-text');
    expect(help.text()).toBe('Het nieuwe adres wordt aurora');
    expect(help.find('code').attributes('translate')).toBe('no');
  });

  describe('what a change comes to', () => {
    function consequences(wrapper: Wrapper) {
      return wrapper.find('[data-testid="group-address-consequences"]');
    }

    it('says the workflow of every site has to change, with the new address in the form the workflow takes', async () => {
      const wrapper = await editing(groupProps());
      expect(consequences(wrapper).exists()).toBe(false);

      type(wrapper, 'aurora', 'group');
      await untilIdle();
      const items = () => consequences(wrapper).findAll('ul')[0]!.findAll('li');
      expect(items()[0]!.text()).toBe(
        'Automatisch publiceren stopt meteen, voor elke site in de groep. Pas site: in je workflow en --site bij plak publish aan naar aurora/<site>, en zet bij elke site haar site-ID erbij als dat er nog niet staat.',
      );
      expect(items()[0]!.findAll('code').map((code) => code.text())).toEqual([
        'site:',
        '--site',
        'plak publish',
        'aurora/<site>',
      ]);
    });

    it('says how many sites get a new address, with an example of what becomes of one', async () => {
      backend.data.sites.push({ ...backend.data.sites[0]!, id: 'x', slug: 'handboek' });
      const wrapper = await editing(groupProps());
      type(wrapper, 'aurora', 'group');
      await untilIdle();

      const items = consequences(wrapper).findAll('ul')[1]!.findAll('li');
      expect(items[0]!.text()).toBe(
        'Alle 2 sites in deze groep krijgen een nieuw adres. Bijvoorbeeld: https://sites.plak.test/team-aurora/website/ wordt https://sites.plak.test/aurora/website/.',
      );
      expect(items[0]!.findAll('code').map((code) => code.text())).toEqual([
        'https://sites.plak.test/team-aurora/website/',
        'https://sites.plak.test/aurora/website/',
      ]);
    });

    it('counts one site as the site, and gives no example of what becomes of it', async () => {
      const wrapper = await editing(groupProps());
      type(wrapper, 'aurora', 'group');
      await untilIdle();

      const items = consequences(wrapper).findAll('ul')[1]!.findAll('li');
      expect(items[0]!.text()).toBe('De site in deze groep krijgt een nieuw adres.');
      expect(items[0]!.find('code').exists()).toBe(false);
    });

    it('leaves the line about the sites out for a group without any', async () => {
      const wrapper = await editing(groupProps({ sites: [] }));
      type(wrapper, 'aurora', 'group');
      await untilIdle();

      const items = consequences(wrapper).findAll('ul')[1]!.findAll('li');
      expect(items.map((item) => item.text())).toEqual([
        'Het oude adres stuurt bezoekers door naar het nieuwe adres, tot en met 7 november 2026. Dat geldt alleen voor wie de site mag bekijken.',
        'Na die datum werkt het oude adres niet meer. Dan kan een andere groep dit adres krijgen, en leidt een oude link naar andere inhoud.',
        'Bezoekers van een afgeschermde site moeten misschien opnieuw inloggen.',
        'Previews krijgen ook een nieuw adres. Links naar previews, bijvoorbeeld in een pull request, werken nog tot en met 7 november 2026.',
        'Tot en met 7 november 2026 kun je het oude adres terugzetten.',
      ]);
    });
  });

  describe('changing the address', () => {
    it('asks the question about the group, with a word about the sites that go along', async () => {
      backend.data.sites.push({ ...backend.data.sites[0]!, id: 'x', slug: 'handboek' });
      const wrapper = await editing(groupProps());

      type(wrapper, 'aurora', 'group');
      await submit(wrapper, 'group');

      expect(dialog(wrapper).props('title')).toBe('Adres van groep team-aurora wijzigen in aurora?');
      expect(dialog(wrapper).props('text')).toBe(
        'Alle 2 sites krijgen een nieuw adres. Oude links sturen door tot en met 7 november 2026. Publiceren vanuit een workflow werkt pas weer nadat je het nieuwe adres instelt.',
      );
    });

    it('speaks of the site when there is one', async () => {
      const wrapper = await editing(groupProps());

      type(wrapper, 'aurora', 'group');
      await submit(wrapper, 'group');

      expect(dialog(wrapper).props('text')).toBe(
        'De site krijgt een nieuw adres. Oude links sturen door tot en met 7 november 2026. Publiceren vanuit een workflow werkt pas weer nadat je het nieuwe adres instelt.',
      );
    });

    it('speaks of nothing but the links for a group without sites', async () => {
      const wrapper = await editing(groupProps({ sites: [] }));

      type(wrapper, 'aurora', 'group');
      await submit(wrapper, 'group');

      expect(dialog(wrapper).props('text')).toBe(
        'Oude links sturen door tot en met 7 november 2026. Publiceren vanuit een workflow werkt pas weer nadat je het nieuwe adres instelt.',
      );
    });

    it('changes the address of the group and passes the group on', async () => {
      const spy = vi.fn(backend.fetch);
      vi.stubGlobal('fetch', spy);
      const wrapper = await editing(groupProps());

      type(wrapper, 'aurora', 'group');
      await submit(wrapper, 'group');
      await confirm(wrapper);

      const [path, init] = spy.mock.calls[0]!;
      expect(path).toBe('/-/api/v1/groups/team-aurora/slug');
      expect(JSON.parse(String(init?.body))).toEqual({ slug: 'aurora' });
      expect(wrapper.emitted('renamed')![0]![0]).toMatchObject({
        slug: 'aurora',
        name: 'Team Aurora',
        previousSlugs: [{ slug: 'team-aurora' }],
      });
      await closeOverlays(wrapper);
      expect(statusLine(wrapper, 'group').text()).toBe(
        'Het adres is gewijzigd. team-aurora stuurt tot en met 7 november 2026 door naar aurora. Gebruik je automatisch publiceren of plak publish? Pas het adres daar nu aan.',
      );
      expect(statusLine(wrapper, 'group').findAll('code').map((code) => code.text())).toEqual([
        'team-aurora',
        'aurora',
        'plak publish',
      ]);
    });

    it('says at the field that the address is taken, in the words about a group', async () => {
      const wrapper = await editing(groupProps());
      type(wrapper, 'aurora', 'group');
      await submit(wrapper, 'group');
      vi.stubGlobal('fetch', problemFetch(409, { title: 'Conflict', code: 'SLUG_EXISTS' }));
      await confirm(wrapper);

      await closeDialog(wrapper);

      expect(serverText(wrapper, 'group')).toBe(
        'Dit adres is al in gebruik of was kort geleden van een andere groep. Kies een ander adres.',
      );
      expect(field(wrapper, 'group').attributes('unmet')).toBe('group-address-server');
    });

    it('says at the field that there are too many old addresses, in the words about a group', async () => {
      const wrapper = await editing(groupProps());
      type(wrapper, 'aurora', 'group');
      await submit(wrapper, 'group');
      vi.stubGlobal('fetch', problemFetch(409, { title: 'Conflict', code: 'TOO_MANY_PREVIOUS_SLUGS' }));
      await confirm(wrapper);

      await closeDialog(wrapper);

      expect(serverText(wrapper, 'group')).toBe(
        'Deze groep heeft al vijf oude adressen die nog doorsturen. Zet een oud adres terug, of wacht tot er een is vrijgekomen.',
      );
    });

    it('changes back to an old address of the group', async () => {
      const before = await toon.setGroupSlug('team-aurora', 'aurora');
      const wrapper = await editing(groupProps({ group: 'aurora', previousSlugs: before.previousSlugs }));

      await wrapper.find('[data-testid="group-address-restore-team-aurora"]').trigger('click');
      await untilIdle();

      expect(dialog(wrapper).props('title')).toBe('Adres van groep aurora wijzigen in team-aurora?');
      await confirm(wrapper);
      expect(backend.data.groups[0]!.slug).toBe('team-aurora');
    });
  });
});
