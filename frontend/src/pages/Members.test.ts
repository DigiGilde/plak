import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter, type Router } from 'vue-router';

import { expectNoAxeViolations } from '../../tests/a11y';
import { makeMockBackend, type MockBackend } from '../api/mock';
import { _resetBreadcrumbs, breadcrumbsFor } from '../composables/breadcrumbs';
import { _resetCurrentMemberCache } from '../composables/currentMember';
import Members from './Members.vue';

let backend: MockBackend;
let router: Router;
let wrapper: ReturnType<typeof mount> | null = null;

beforeEach(() => {
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
  _resetBreadcrumbs();
  _resetCurrentMemberCache();
  router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/-/members', component: Members }],
  });
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.unstubAllGlobals();
});

async function mountComponent(): Promise<ReturnType<typeof mount>> {
  await router.push('/-/members');
  await router.isReady();
  wrapper = mount(Members, { global: { plugins: [router] }, attachTo: document.body });
  await waitUntilLoaded();
  return wrapper;
}

/**
 * Waits until the page has left the "laden" state. Looks at the element, not at
 * text: nldd-activity-indicator renders its label in its shadow DOM, so
 * `wrapper.text()` (light DOM only) never shows the loading text. On top of
 * that, nldd-* elements sometimes only reflect properties after an extra update
 * round ("scheduled an update after an update completed" in the test logs), so
 * poll rather than use a fixed timeout.
 */
async function waitUntilLoaded(): Promise<void> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    await flushPromises();
    await new Promise((resolve) => setTimeout(resolve, 10));
    if (wrapper && !wrapper.find('nldd-activity-indicator').exists()) {
      return;
    }
  }
}

/**
 * nldd-text-cell, nldd-tag, nldd-banner and nldd-inline-dialog render their
 * `text` / `supporting-text` in their own shadow DOM; they are attributes on the
 * element, not light-DOM text nodes. So `wrapper.text()` (textContent) never
 * sees them. The serialised HTML does carry the attribute value, so that is what
 * we assert content on.
 */
function html(): string {
  return wrapper!.html();
}

/** A body row: the header row carries slot="header" and matches no member. */
function rowWith(substring: string) {
  return wrapper!.findAll('nldd-table-row').find((row) => row.html().includes(substring));
}

/**
 * The members in the order the page actually renders them. The name sits on the
 * first cell of a row as a `text` attribute, so read that cell rather than the
 * row's serialised HTML, which also carries the status tag and the dates.
 */
function renderedNames(): string[] {
  return wrapper!
    .findAll('nldd-table-row:not([slot="header"])')
    .map((row) => row.find('nldd-text-cell').attributes('text') ?? '')
    .filter((name) => name !== '');
}

/** Clicks the sort button inside the header cell with this label. */
async function sortOn(label: string): Promise<void> {
  const header = wrapper!
    .find('nldd-table-row[slot="header"]')
    .findAll('nldd-text-cell')
    .find((cell) => cell.text().includes(label));
  expect(header?.exists(), `column header "${label}" missing`).toBe(true);
  await header!.find('button').trigger('click');
  await flushPromises();
}

/** The aria-sort the header cell with this label carries. */
function ariaSortOf(label: string): string | undefined {
  return wrapper!
    .find('nldd-table-row[slot="header"]')
    .findAll('nldd-text-cell')
    .find((cell) => cell.text().includes(label))
    ?.attributes('aria-sort');
}

/**
 * The action of one row, from that row's own menu. Scoped to the row on
 * purpose: every row carries its own menu in the light DOM, so an unscoped
 * query finds the first one in the document, not the one you meant.
 */
function actionOf(name: string, testid: string) {
  return rowWith(name)!.find(`[data-testid="${testid}"]`);
}

async function runAction(name: string, testid: string): Promise<void> {
  const item = actionOf(name, testid);
  expect(item.exists(), `action "${testid}" missing for ${name}`).toBe(true);
  item.element.dispatchEvent(new CustomEvent('select'));
  await waitUntilLoaded();
}

async function search(term: string): Promise<void> {
  const field = wrapper!.find('[data-testid="leden-zoeken"]');
  field.element.dispatchEvent(new CustomEvent('input', { detail: { value: term } }));
  await flushPromises();
}

describe('Platform management (filled)', () => {
  it('shows the seeded members with the action available for them', async () => {
    await mountComponent();

    expect(html()).toContain('Bea Heerder');
    expect(html()).toContain('Wim Weg');
    expect(actionOf('Wim Weg', 'lid-toegang-lid-2').attributes('text')).toBe('Toegang teruggeven');
  });

  it('puts the members in a table with fixed columns, so every row aligns on the same x', async () => {
    const app = await mountComponent();

    const table = app.find('nldd-table');
    expect(table.exists()).toBe(true);
    expect(table.attributes('columns')).toBe('minmax(12rem, 1fr) 9rem 6.5rem 8.5rem 3rem');
    // Five cells per row, in the order of the header row: member, role,
    // created, last activity, actions. No status column: the state sits
    // on the member's own overline.
    const cells = rowWith('Wim Weg')!.findAll('nldd-text-cell, nldd-cell');
    expect(cells).toHaveLength(5);
    expect(rowWith('Wim Weg')!.html()).not.toContain('nldd-tag');
  });

  it('names the action after what happens to the person, not after the database column', async () => {
    await mountComponent();

    // Two states, two verbs, all about access.
    expect(actionOf('Ada Vermeer', 'lid-toegang-lid-3').attributes('text')).toBe('Toegang intrekken');
    expect(actionOf('Karel Oud', 'lid-toegang-lid-5').attributes('text')).toBe('Toegang teruggeven');
  });

  it('puts where someone stands in the role column, and colors it', async () => {
    await mountComponent();

    // The second cell of the row is the role column.
    const standing = (name: string) => rowWith(name)!.findAll('nldd-text-cell')[1]!;
    expect(standing('Bea Heerder').attributes('text')).toBe('Platformbeheerder');
    expect(standing('Ada Vermeer').attributes('text')).toBe('Lid');

    // Without access the stored role says nothing, so the column says
    // what is true instead. The color repeats the words, it does not
    // carry them (WCAG 1.4.1).
    expect(standing('Karel Oud').attributes('text')).toBe('Geen toegang');
    expect(standing('Karel Oud').attributes('color')).toBe('critical');
  });

  it('offers no actions on your own row, because the API refuses all of them', async () => {
    await mountComponent();

    // The mock logs in as lid-1, Bea Heerder.
    expect(rowWith('Bea Heerder')!.findAll('nldd-icon-button')).toHaveLength(0);
    expect(rowWith('Ada Vermeer')!.findAll('nldd-icon-button')).toHaveLength(1);
  });

  it('leaves the bootstrap account visible with the reason included, instead of leaving it out', async () => {
    // Bea Heerder is the bootstrap account; viewed by someone else the
    // action stays visible but disabled, because that reason is not
    // self-evident.
    backend.data.loggedInMemberId = 'lid-3';
    const app = await mountComponent();

    const item = actionOf('Bea Heerder', 'lid-toegang-lid-1');
    expect(item.exists()).toBe(true);
    expect(item.attributes('disabled')).toBeDefined();
    expect(item.attributes('details')).toBe('bootstrap-account');
    expect(app.exists()).toBe(true);
  });

  it('shows who is platform admin and makes that changeable from the menu', async () => {
    await mountComponent();

    // The role sits as a fact in its own column; the button beside it is
    // about the status, not the role.
    expect(rowWith('Bea Heerder')!.html()).toContain('text="Platformbeheerder"');
    expect(rowWith('Ada Vermeer')!.html()).toContain('text="Lid"');

    expect(actionOf('Ada Vermeer', 'lid-rol-lid-3').attributes('text')).toBe('Maak platformbeheerder');

    await runAction('Ada Vermeer', 'lid-rol-lid-3');

    expect(backend.data.members.find((l) => l.id === 'lid-3')?.platformRole).toBe('admin');
    // And the menu now offers the reverse.
    expect(actionOf('Ada Vermeer', 'lid-rol-lid-3').attributes('text')).toBe('Beheerdersrol afnemen');
  });

  it('names the action column for a screen reader without putting the heading on screen', async () => {
    const app = await mountComponent();

    const last = app.find('nldd-table-row[slot="header"]').findAll('nldd-text-cell').at(-1)!;
    // No visible text: at 3rem "Acties" wrapped over two lines.
    expect(last.attributes('text')).toBeUndefined();
    // A name, though, otherwise the column header is an axe violation.
    expect(last.find('.alleen-schermlezer').text()).toBe('Acties');
  });

  it('gives every row one menu, right-aligned on the same edge', async () => {
    const app = await mountComponent();

    const headers = app.find('nldd-table-row[slot="header"]').findAll('nldd-text-cell');
    expect(headers.at(-1)!.attributes('horizontal-alignment')).toBe('right');
    expect(rowWith('Ada Vermeer')!.find('nldd-cell').attributes('horizontal-alignment')).toBe('right');
    // One control per row, no separate buttons beside it.
    expect(rowWith('Ada Vermeer')!.findAll('nldd-button')).toHaveLength(0);
    expect(rowWith('Ada Vermeer')!.findAll('nldd-icon-button')).toHaveLength(1);
  });

  it('shows per member when the account was created and when the member last visited', async () => {
    await mountComponent();

    const row = rowWith('Bea Heerder')!.html();
    // formatDate/formatTimestamp run on nl-NL: a medium date contains the
    // month as a word, and the time is added after a comma.
    expect(row).toMatch(/text="\d{1,2} \w+ 2026"/);
    expect(row).toMatch(/text="\d{1,2} \w+ 2026, \d{2}:\d{2}"/);
  });

  it('sorts alphabetically by default and further via a click on the column header', async () => {
    await mountComponent();

    const alphabetical = renderedNames();
    expect(alphabetical).toEqual([...alphabetical].sort((a, b) => a.localeCompare(b, 'nl')));
    expect(alphabetical[0]).toBe('Ada Vermeer');
    expect(ariaSortOf('Lid')).toBe('ascending');
    expect(ariaSortOf('Aangemaakt')).toBe('none');

    // A date column starts at the newest: Wim Weg is the youngest account.
    await sortOn('Aangemaakt');
    expect(renderedNames()[0]).toBe('Wim Weg');
    expect(ariaSortOf('Aangemaakt')).toBe('descending');
    expect(ariaSortOf('Lid')).toBe('none');

    // Another click on the same header reverses the order.
    await sortOn('Aangemaakt');
    expect(renderedNames()[0]).toBe('Karel Oud');
    expect(ariaSortOf('Aangemaakt')).toBe('ascending');

    await sortOn('Laatste activiteit');
    // Whoever never logged in has no activity and sinks to the bottom,
    // whichever way the column is sorted.
    expect(renderedNames().at(-1)).toBe('Wim Weg');
    await sortOn('Laatste activiteit');
    expect(renderedNames().at(-1)).toBe('Wim Weg');
  });

  it('searches by name and by email address', async () => {
    await mountComponent();

    await search('zoë');
    expect(renderedNames()).toEqual(['Zoë de Wit']);

    await search('oud@voorbeeld.nl');
    expect(renderedNames()).toEqual(['Karel Oud']);

    await search('bestaat niet');
    expect(renderedNames()).toEqual([]);
    expect(html()).toContain('Geen lid gevonden.');

    await search('');
    expect(renderedNames().length).toBeGreaterThan(1);
  });

  it('supplies the breadcrumb to the app shell instead of putting it at the top itself', async () => {
    const app = await mountComponent();

    expect(app.find('nldd-breadcrumbs').exists()).toBe(false);
    expect(breadcrumbsFor('/-/members')).toEqual([
      { text: 'Overzicht', href: '/' },
      { text: 'Platformbeheer' },
    ]);
  });

  it('gives a deactivated member access back', async () => {
    await mountComponent();

    await runAction('Wim Weg', 'lid-toegang-lid-2');

    expect(backend.data.members.find((l) => l.id === 'lid-2')?.status).toBe('active');
    // Access restored, so the menu now offers the reverse and the
    // overline is gone.
    expect(actionOf('Wim Weg', 'lid-toegang-lid-2').attributes('text')).toBe('Toegang intrekken');
    expect(rowWith('Wim Weg')!.findAll('nldd-text-cell')[1]!.attributes('text')).toBe('Lid');
  });

  it('shows the new status immediately and confirms in the background', async () => {
    let release!: () => void;
    const realFetch = backend.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (...args: Parameters<typeof fetch>) => {
        if (String(args[0]).includes('_activate')) {
          await new Promise<void>((resolve) => {
            release = resolve;
          });
        }
        return realFetch(...args);
      }),
    );
    await mountComponent();

    actionOf('Wim Weg', 'lid-toegang-lid-2').element.dispatchEvent(new CustomEvent('select'));
    await flushPromises();

    expect(actionOf('Wim Weg', 'lid-toegang-lid-2').attributes('text')).toBe('Toegang intrekken');
    expect(backend.data.members.find((l) => l.id === 'lid-2')?.status).toBe('deactivated');

    release();
    await waitUntilLoaded();

    expect(backend.data.members.find((l) => l.id === 'lid-2')?.status).toBe('active');
  });

  it('rolls back and reports it when the action fails', async () => {
    const realFetch = backend.fetch;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (...args: Parameters<typeof fetch>) => {
        if (String(args[0]).includes('_activate')) {
          return new Response(
            JSON.stringify({ type: 'about:blank', title: 'Interne fout', status: 500 }),
            { status: 500, headers: { 'content-type': 'application/problem+json' } },
          );
        }
        return realFetch(...args);
      }),
    );
    await mountComponent();

    await runAction('Wim Weg', 'lid-toegang-lid-2');

    expect(actionOf('Wim Weg', 'lid-toegang-lid-2').attributes('text')).toBe('Toegang teruggeven');
    // nldd-notification moves itself into its own region in the body, so it no
    // longer sits under the page's element.
    const notice = document.querySelector('nldd-notification') as
      | (HTMLElement & { text?: string; variant?: string })
      | null;
    expect(notice?.text).toBe('Wim Weg activeren is niet gelukt');
    expect(notice?.variant).toBe('critical');
  });

  it('revokes access from an active member', async () => {
    await mountComponent();

    await runAction('Ada Vermeer', 'lid-toegang-lid-3');

    expect(backend.data.members.find((l) => l.id === 'lid-3')?.status).toBe('deactivated');
    // Revoked reads differently from pending: this account was on before.
    expect(actionOf('Ada Vermeer', 'lid-toegang-lid-3').attributes('text')).toBe('Toegang teruggeven');
    expect(rowWith('Ada Vermeer')!.findAll('nldd-text-cell')[1]!.attributes('text')).toBe(
      'Geen toegang',
    );
  });

  it('has no axe violations', async () => {
    const app = await mountComponent();

    await expectNoAxeViolations(app.element);
  });
});

describe('Platform management (empty)', () => {
  it("puts the empty state in the table's empty slot", async () => {
    backend = makeMockBackend({ ...makeMockBackend().data, members: [] });
    vi.stubGlobal('fetch', backend.fetch);

    const app = await mountComponent();

    expect(app.find('nldd-table').exists()).toBe(true);
    expect(app.findAll('nldd-table-row:not([slot="header"])')).toHaveLength(0);
    const empty = app.find('nldd-inline-dialog[slot="empty"]');
    expect(empty.exists()).toBe(true);
    expect(empty.html()).toContain('nog geen leden');
  });
});

describe('Platform management (error)', () => {
  it('shows an error message with retry when fetching fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ type: 'about:blank', title: 'Interne fout', status: 500 }),
          { status: 500, headers: { 'content-type': 'application/problem+json' } },
        ),
      ),
    );

    const app = await mountComponent();

    expect(html()).toContain('Interne fout');
    const retryButton = app.find('nldd-button[text="Opnieuw proberen"]');
    expect(retryButton.exists()).toBe(true);

    // Recovery: a second attempt against a working backend succeeds.
    vi.stubGlobal('fetch', backend.fetch);
    await retryButton.trigger('click');
    await waitUntilLoaded();

    expect(html()).toContain('Bea Heerder');
  });
});
