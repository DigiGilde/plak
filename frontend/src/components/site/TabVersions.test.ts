import { mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { makeMockBackend, MOCK_CONTENT_BASE, type MockBackend } from '@/api/mock';
import { _resetCurrentMemberCache } from '@/composables/currentMember';
import TabVersions from './TabVersions.vue';
import { fireDetailEvent, serverErrorFetch, untilIdle } from './testHelpers';

let backend: MockBackend;

beforeEach(() => {
  _resetCurrentMemberCache();
  backend = makeMockBackend();
  vi.stubGlobal('fetch', backend.fetch);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function makeWrapper() {
  return mount(TabVersions, {
    props: { group: 'team-aurora', site: 'website', contentBase: MOCK_CONTENT_BASE },
  });
}

/** A menu item fires `select`, not `click`. */
async function runAction(wrapper: ReturnType<typeof makeWrapper>, testid: string): Promise<void> {
  const item = wrapper.find(`[data-testid="${testid}"]`);
  expect(item.exists(), `action "${testid}" is missing`).toBe(true);
  item.element.dispatchEvent(new CustomEvent('select'));
  await untilIdle();
}

describe('TabVersions: states', () => {
  it('shows only the live history, with a marker on the current live version', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versie-versie-1"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="versie-versie-0"]').exists()).toBe(true);
    // The preview version does not belong in this list.
    expect(wrapper.find('[data-testid="versie-versie-preview-42"]').exists()).toBe(false);

    // Every row carries the marker; the color and the accessible label say
    // which of the two states it is.
    const marker = (id: string) => wrapper.find(`[data-testid="live-marker-${id}"]`);
    expect(marker('versie-1').attributes('color')).toBe('success');
    expect(marker('versie-1').attributes('accessible-label')).toBe('Staat nu live');
    expect(marker('versie-0').attributes('color')).toBe('neutral');
    expect(marker('versie-0').attributes('accessible-label')).toBe('Staat niet live');
  });

  it('opens a version in a new tab, and says so', async () => {
    const open = vi.fn();
    vi.stubGlobal('open', open);

    const wrapper = makeWrapper();
    await untilIdle();

    // WCAG 3.2.5: announce a new window before it opens.
    expect(wrapper.find('[data-testid="bekijk-versie-0"]').attributes('details')).toBe(
      'nieuw tabblad',
    );

    await runAction(wrapper, 'bekijk-versie-0');

    expect(open).toHaveBeenCalledWith(
      'https://sites.plak.test/team-aurora/website/_version/versie-0/',
      '_blank',
      'noopener',
    );
  });

  it('renders the version list and its skeleton as box-tinted', async () => {
    const loadWrapper = makeWrapper();
    // Not finished loading: this is the skeleton behind the indicator.
    expect(loadWrapper.find('[data-testid="versies-skelet"]').attributes('variant')).toBe(
      'box-tinted',
    );

    const wrapper = makeWrapper();
    await untilIdle();

    // The tab sits on the same plain page background as the site overview;
    // box-base is for a list on an already tinted parent.
    expect(wrapper.find('[data-testid="versions-list"]').attributes('variant')).toBe('box-tinted');
  });

  it('lets the marker open the row and keeps a single menu on the right', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    // The marker is the first cell, then a 12 spacer, only then the text.
    const live = wrapper.find('[data-testid="versie-versie-1"]');
    const cellen = [...live.element.children].map((c) => c.tagName.toLowerCase());
    expect(cellen[0]).toBe('nldd-cell');
    expect(live.find('nldd-cell').find('nldd-badge').exists()).toBe(true);
    expect(cellen[1]).toBe('nldd-spacer-cell');
    expect(cellen[2]).toBe('nldd-text-cell');
    expect(cellen[3]).toBe('nldd-cell');
    expect(live.findAll('nldd-icon-button')).toHaveLength(1);

    // Two separate buttons did not fit: at 320px and 200% text "Zet deze
    // versie live" stuck out 151px beyond the screen (WCAG 1.4.10). The
    // number of actions may no longer touch the row's width.
    const older = wrapper.find('[data-testid="versie-versie-0"]');
    expect(older.findAll('nldd-icon-button')).toHaveLength(1);
    expect(older.findAll('nldd-spacer-cell').map((c) => c.attributes('size'))).toEqual(['12']);
  });

  it('renders the rows with controls as a form list, without a disabled action', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versions-list"]').attributes('type')).toBe('form');
    expect(
      wrapper.find('[data-testid="live-zetten-versie-0"]').attributes('disabled'),
    ).toBeUndefined();
  });

  it('shows a static skeleton behind the delayed indicator while loading', async () => {
    vi.stubGlobal('fetch', () => new Promise<Response>(() => {}));

    const wrapper = makeWrapper();
    await untilIdle();

    const indicator = wrapper.find('nldd-activity-indicator');
    expect(indicator.exists()).toBe(true);
    // The default timing="delay" holds the indicator back 1000 ms; do not override.
    expect(indicator.attributes('timing')).toBeUndefined();

    // The skeleton is inside it, so the indicator dims that rather than filling
    // an empty screen; the heading and the list shape are already there.
    const skeleton = indicator.find('[data-testid="versies-skelet"]');
    expect(skeleton.exists()).toBe(true);
    expect(skeleton.attributes('aria-hidden')).toBe('true');
    expect(skeleton.attributes('type')).toBe('form');
    expect(skeleton.findAll('nldd-list-item')).toHaveLength(3);
    expect(wrapper.find('#kop-versies').exists()).toBe(true);
  });

  it('clears the skeleton once the versions are in', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versies-skelet"]').exists()).toBe(false);
    expect(wrapper.find('nldd-activity-indicator').exists()).toBe(false);
  });

  it('names the row in the menu\'s accessible label', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const row = wrapper.find('[data-testid="versie-versie-0"]');
    const stamp = row.find('nldd-text-cell').attributes('text');
    expect(row.find('nldd-icon-button').attributes('accessible-label')).toBe(
      `Acties voor ${stamp}`,
    );
    expect(wrapper.find('[data-testid="live-zetten-versie-0"]').attributes('text')).toBe(
      'Zet deze versie live',
    );
  });

  it('names the repository for a CI deploy, not "Gepubliceerd door CI"', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const row = wrapper.find('[data-testid="versie-versie-1"]');
    expect(row.find('nldd-text-cell').attributes('supporting-text')).toBe(
      'Gepubliceerd door github.com/team-aurora/website',
    );
  });

  it('falls back to "Handmatig geüpload" without a known uploader', async () => {
    const seeded = backend.data.versions.find((v) => v.id === 'versie-0')!;
    seeded.createdByName = null;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(
      wrapper.find('[data-testid="versie-versie-0"]').find('nldd-text-cell').attributes('supporting-text'),
    ).toBe('Handmatig geüpload');
  });

  it('falls back to "Gepubliceerd door CI" without a known repository', async () => {
    const seeded = backend.data.versions.find((v) => v.id === 'versie-1')!;
    seeded.createdByRepository = null;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(
      wrapper.find('[data-testid="versie-versie-1"]').find('nldd-text-cell').attributes('supporting-text'),
    ).toBe('Gepubliceerd door CI');
  });

  it('shows the empty state without live versions', async () => {
    backend.data.versions = [];

    const wrapper = makeWrapper();
    await untilIdle();

    const empty = wrapper.find('nldd-inline-dialog[data-testid="versies-leeg"]');
    expect(empty.exists()).toBe(true);
    expect(empty.attributes('text')).toBe('Nog geen live-versies');
    expect(empty.attributes('supporting-text')).toContain('tabblad Overzicht');
  });

  it('shows the usage and the retention rule above the list', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versions-storage"]').text()).toBe(
      "Deze site gebruikt 74 MB van 500 MB. De huidige en de 5 vorige versies blijven bewaard. Oudere worden 's nachts opgeruimd.",
    );
  });

  it('does not show the usage while loading', async () => {
    vi.stubGlobal('fetch', () => new Promise<Response>(() => {}));

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versions-storage"]').exists()).toBe(false);
  });

  it('words a retention of one as "the previous version"', async () => {
    backend.data.defaultLiveVersionsKept = 1;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versions-storage"]').text()).toContain(
      "De huidige en de vorige versie blijven bewaard. Oudere worden 's nachts opgeruimd.",
    );
  });

  it('says all versions are kept when the retention is off', async () => {
    backend.data.defaultLiveVersionsKept = 0;

    const wrapper = makeWrapper();
    await untilIdle();

    const text = wrapper.find('[data-testid="versions-storage"]').text();
    expect(text).toContain('Alle versies blijven bewaard.');
    expect(text).not.toContain('opgeruimd');
  });

  it('says so when the retention is the site\'s own setting', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;

    const wrapper = makeWrapper();
    await untilIdle();

    const text = wrapper.find('[data-testid="versions-storage"]').text();
    expect(text).toContain('De huidige en de 3 vorige versies blijven bewaard');
    expect(text).toContain('Dit is een eigen instelling van deze site.');
  });

  it('does not mention an own setting while the site follows the default', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versions-storage"]').text()).not.toContain('eigen instelling');
  });

  it('leaves out the quota when there is none', async () => {
    backend.data.storage.maxBytes = 0;

    const wrapper = makeWrapper();
    await untilIdle();

    const text = wrapper.find('[data-testid="versions-storage"]').text();
    expect(text).toContain('Deze site gebruikt 74 MB. ');
    expect(text).not.toContain(' van ');
  });

  it('keeps the list when the usage cannot be loaded, without an error banner', async () => {
    const inner = backend.fetch;
    vi.stubGlobal('fetch', (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith('/storage') ? serverErrorFetch()(input, init) : inner(input, init),
    );

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versions-storage"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="versie-versie-1"]').exists()).toBe(true);
    expect(wrapper.html()).not.toContain('Serverfout');
  });

  it('shows the usage on the empty state too', async () => {
    backend.data.versions = [];

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versies-leeg"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="versions-storage"]').text()).toContain('Deze site gebruikt');
  });

  it('shows an error message on a server error', async () => {
    vi.stubGlobal('fetch', serverErrorFetch());

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.html()).toContain('Serverfout');
  });
});

describe('TabVersions: setting live', () => {
  it('sets an older version live and moves the marker', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    // The current live version has no "live zetten" action.
    expect(wrapper.find('[data-testid="live-zetten-versie-1"]').exists()).toBe(false);

    await runAction(wrapper, 'live-zetten-versie-0');

    expect(backend.data.sites[0]!.liveVersionId).toBe('versie-0');
    expect(wrapper.find('[data-testid="live-marker-versie-0"]').attributes('color')).toBe('success');
    expect(wrapper.find('[data-testid="live-marker-versie-1"]').attributes('color')).toBe('neutral');
    expect(wrapper.emitted('changed')).toBeTruthy();
    expect(wrapper.find('nldd-notification[text="Versie live gezet"]').exists()).toBe(true);
  });

  it('reports it when setting live fails', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', serverErrorFetch());
    await runAction(wrapper, 'live-zetten-versie-0');

    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Versie niet live gezet');
    expect(wrapper.html()).toContain('Serverfout');
  });

  it('reports a generic failure when setting live throws something other than an ApiError', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('network down')));
    await runAction(wrapper, 'live-zetten-versie-0');

    const notice = wrapper.find('nldd-notification[variant="critical"]');
    expect(notice.attributes('text')).toBe('Versie niet live gezet');
    expect(notice.attributes('supporting-text')).toBe('Live zetten is niet gelukt.');
  });
});

describe('TabVersions: previous versions kept', () => {
  type Wrapper = ReturnType<typeof makeWrapper>;
  const field = (wrapper: Wrapper) => wrapper.find('[data-testid="retention-count"]');
  const type = (wrapper: Wrapper, value: string): void =>
    fireDetailEvent(field(wrapper).element, 'input', { value });
  const commit = async (wrapper: Wrapper, value: string): Promise<void> => {
    fireDetailEvent(field(wrapper).element, 'change', { value });
    await untilIdle();
  };
  const choose = async (wrapper: Wrapper, testid: string): Promise<void> => {
    fireDetailEvent(wrapper.find(`[data-testid="${testid}"]`).element, 'change', {});
    await untilIdle();
  };
  const usage = (wrapper: Wrapper): string => wrapper.find('[data-testid="versions-storage"]').text();
  const saved = (wrapper: Wrapper) => wrapper.findAll('nldd-notification[text="Bewaarde versies opgeslagen"]');
  /** Counts the PUTs to the setting, passing every request on to the mock. */
  function countPuts(): { count: () => number } {
    let puts = 0;
    const inner = backend.fetch;
    vi.stubGlobal('fetch', (request: RequestInfo | URL, init?: RequestInit) => {
      if (String(request).endsWith('/live-versions-kept')) puts += 1;
      return inner(request, init);
    });
    return { count: () => puts };
  }
  function refuseWith(problem: Record<string, unknown>, status = 422): void {
    vi.stubGlobal('fetch', () =>
      Promise.resolve(
        new Response(JSON.stringify({ type: 'about:blank', status, ...problem }), {
          status,
          headers: { 'content-type': 'application/problem+json' },
        }),
      ),
    );
  }
  const fieldError = (wrapper: Wrapper): string => wrapper.find('#retention-invalid').text();

  it('offers the platform default and a custom number in one box, the field only with custom', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    const box = wrapper.find('[data-testid="retention-box"]');
    expect(box.attributes('type')).toBe('form');
    expect(box.attributes('variant')).toBe('box-tinted');
    const choice = box.find('[data-testid="retention-choice"]');
    expect(choice.attributes('type')).toBe('radiogroup');
    expect(choice.attributes('dividers')).toBe('never');
    expect(choice.attributes('accessible-label')).toBe('Bewaarde vorige versies');
    expect(wrapper.find('[data-testid="retention-default"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('[data-testid="retention-custom"]').attributes('checked')).toBeUndefined();
    const labels = wrapper.findAll('[data-testid="retention-choice"] nldd-title-cell');
    expect(labels.map((cell) => cell.attributes('text'))).toEqual([
      'Standaard: 5 vorige versies',
      'Aangepast aantal',
    ]);
    expect(labels.map((cell) => cell.attributes('supporting-text'))).toEqual([
      'Volgt de instelling van het platform.',
      'Een ander aantal voor deze site.',
    ]);
    expect(wrapper.find('[data-testid="retention-count-row"]').exists()).toBe(false);
    expect(wrapper.find('nldd-button').exists()).toBe(false);
  });

  it('words a platform default of one and of 0', async () => {
    backend.data.defaultLiveVersionsKept = 1;
    let wrapper = makeWrapper();
    await untilIdle();
    expect(wrapper.find('[data-testid="retention-default"] nldd-title-cell').attributes('text')).toBe(
      'Standaard: 1 vorige versie',
    );

    backend.data.defaultLiveVersionsKept = 0;
    wrapper = makeWrapper();
    await untilIdle();
    expect(wrapper.find('[data-testid="retention-default"] nldd-title-cell').attributes('text')).toBe(
      'Standaard: alle versies',
    );
  });

  it('shows an own number as chosen, in the field under the custom option, with its label and help', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="retention-custom"]').attributes('checked')).toBeDefined();
    const row = wrapper.find('[data-testid="retention-count-row"]');
    // A row of its own in the box, outside the radiogroup: never inside a radio row.
    expect(row.element.parentElement?.getAttribute('data-testid')).toBe('retention-box');
    expect(row.findAll('nldd-spacer-cell').map((cell) => cell.attributes('size'))).toEqual(['24', '12']);
    expect(row.find('nldd-form-field').attributes('label')).toBe('Aantal vorige versies');
    expect(row.find('nldd-form-field-help-text').text()).toBe('0 bewaart alle versies.');
    expect(field(wrapper).attributes('value')).toBe('3');
  });

  it('saves the default as soon as it is chosen, and the usage line follows', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();

    await choose(wrapper, 'retention-default');

    expect(backend.data.sites[0]!.liveVersionsKept).toBeNull();
    expect(wrapper.find('[data-testid="retention-default"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('[data-testid="retention-count-row"]').exists()).toBe(false);
    expect(saved(wrapper)[0]!.attributes('supporting-text')).toContain('de 5 vorige versies');
    expect(usage(wrapper)).toContain('de 5 vorige versies');
    expect(usage(wrapper)).not.toContain('eigen instelling');
  });

  it('starts a custom number from the default and saves it as the own number right away', async () => {
    const wrapper = makeWrapper();
    await untilIdle();

    await choose(wrapper, 'retention-custom');

    expect(backend.data.sites[0]!.liveVersionsKept).toBe(5);
    expect(field(wrapper).attributes('value')).toBe('5');
    expect(saved(wrapper)).toHaveLength(1);
    expect(usage(wrapper)).toContain('Dit is een eigen instelling van deze site.');
  });

  it('sends nothing when an option is chosen that is already in force', async () => {
    const puts = countPuts();
    const wrapper = makeWrapper();
    await untilIdle();
    await choose(wrapper, 'retention-default');
    expect(puts.count()).toBe(0);

    backend.data.sites[0]!.liveVersionsKept = 3;
    const own = makeWrapper();
    await untilIdle();
    await choose(own, 'retention-custom');
    expect(puts.count()).toBe(0);
    expect(field(own).attributes('value')).toBe('3');
  });

  it('saves a number on change, not while it is typed', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const puts = countPuts();
    const wrapper = makeWrapper();
    await untilIdle();

    type(wrapper, '7');
    await untilIdle();
    expect(puts.count()).toBe(0);
    expect(backend.data.sites[0]!.liveVersionsKept).toBe(3);

    await commit(wrapper, '7');

    expect(puts.count()).toBe(1);
    expect(backend.data.sites[0]!.liveVersionsKept).toBe(7);
    expect(saved(wrapper)[0]!.attributes('supporting-text')).toContain('de 7 vorige versies');
    expect(usage(wrapper)).toContain('de 7 vorige versies');
  });

  it('does not send a number that did not change', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const puts = countPuts();
    const wrapper = makeWrapper();
    await untilIdle();

    await commit(wrapper, '3');

    expect(puts.count()).toBe(0);
    expect(saved(wrapper)).toHaveLength(0);
  });

  it('saves 0 as keep all', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();

    await commit(wrapper, '0');

    expect(backend.data.sites[0]!.liveVersionsKept).toBe(0);
    expect(saved(wrapper)[0]!.attributes('supporting-text')).toBe('Alle versies blijven bewaard.');
  });

  it.each(['-1', '2.5', '', 'abc', '1e3'])('refuses %j and sends nothing', async (value) => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const puts = countPuts();
    const wrapper = makeWrapper();
    await untilIdle();

    await commit(wrapper, value);

    expect(field(wrapper).attributes('invalid')).toBeDefined();
    expect(field(wrapper).attributes('unmet')).toBe('retention-invalid');
    expect(wrapper.find('#retention-invalid').text()).toBe('Vul een geheel getal van 0 of meer in.');
    expect(puts.count()).toBe(0);
    expect(backend.data.sites[0]!.liveVersionsKept).toBe(3);
  });

  it('clears the message once a valid number is committed', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();
    await commit(wrapper, '-1');

    await commit(wrapper, '5000');

    expect(field(wrapper).attributes('invalid')).toBeUndefined();
    expect(backend.data.sites[0]!.liveVersionsKept).toBe(5000);
  });

  it('shows a number the database cannot hold at the field, keeping it there to correct', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();

    await commit(wrapper, '5000000001');

    expect(field(wrapper).attributes('invalid')).toBeDefined();
    expect(field(wrapper).attributes('unmet')).toBe('retention-invalid');
    expect(fieldError(wrapper)).toBe('Dit getal is te groot om op te slaan. Kies een kleiner aantal.');
    expect(field(wrapper).attributes('value')).toBe('5000000001');
    expect(wrapper.find('[data-testid="retention-custom"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
    expect(backend.data.sites[0]!.liveVersionsKept).toBe(3);
    expect(usage(wrapper)).toContain('de 3 vorige versies');
  });

  it('shows a refused number at the field with the detail the server gave', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();

    refuseWith({ title: 'Ongeldig aantal', detail: 'Dat aantal mag niet.', code: 'LIVE_VERSIONS_KEPT_INVALID' });
    await commit(wrapper, '7');

    expect(fieldError(wrapper)).toBe('Dat aantal mag niet.');
    expect(field(wrapper).attributes('value')).toBe('7');
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
  });

  it('falls back to the title at the field when the refusal has no detail', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();

    refuseWith({ title: 'Te groot aantal', code: 'LIVE_VERSIONS_KEPT_TOO_LARGE' });
    await commit(wrapper, '7');

    expect(fieldError(wrapper)).toBe('Te groot aantal');
  });

  it('takes the field error back as soon as the number is edited', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();
    await commit(wrapper, '5000000001');

    type(wrapper, '500000000');
    await untilIdle();

    expect(field(wrapper).attributes('invalid')).toBeUndefined();
    expect(field(wrapper).attributes('value')).toBe('500000000');
  });

  it('takes the field error back on the next successful save', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();
    await commit(wrapper, '5000000001');

    await commit(wrapper, '8');

    expect(field(wrapper).attributes('invalid')).toBeUndefined();
    expect(backend.data.sites[0]!.liveVersionsKept).toBe(8);
    expect(saved(wrapper)).toHaveLength(1);
  });

  it('takes the field error back when the default is chosen', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();
    await commit(wrapper, '5000000001');

    await choose(wrapper, 'retention-default');
    await choose(wrapper, 'retention-custom');

    expect(field(wrapper).attributes('invalid')).toBeUndefined();
  });

  it('keeps the number on any other refusal and says why at the field, without a notice', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();

    refuseWith({ title: 'Geen toegang', detail: 'Je rol is te smal.', code: 'INSUFFICIENT_ROLE' }, 403);
    await commit(wrapper, '7');

    expect(fieldError(wrapper)).toBe('Niet opgeslagen: Je rol is te smal. Probeer het opnieuw.');
    expect(field(wrapper).attributes('value')).toBe('7');
    expect(wrapper.find('[data-testid="retention-custom"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
    // The sentence above the box tells what is saved, not what was typed.
    expect(usage(wrapper)).toContain('de 3 vorige versies');
  });

  it('uses the title of a refusal without a detail, also for a 422 with another code', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();

    refuseWith({ title: 'Ongeldige invoer' });
    await commit(wrapper, '7');

    expect(fieldError(wrapper)).toBe('Niet opgeslagen: Ongeldige invoer. Probeer het opnieuw.');
    expect(field(wrapper).attributes('value')).toBe('7');
  });

  it('keeps the number on a server error and saves it when committed again', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();
    const working = backend.fetch;

    vi.stubGlobal('fetch', serverErrorFetch());
    await commit(wrapper, '7');
    expect(fieldError(wrapper)).toBe('Niet opgeslagen: Serverfout. Probeer het opnieuw.');
    expect(field(wrapper).attributes('value')).toBe('7');

    vi.stubGlobal('fetch', working);
    await commit(wrapper, '7');

    expect(field(wrapper).attributes('invalid')).toBeUndefined();
    expect(backend.data.sites[0]!.liveVersionsKept).toBe(7);
    expect(usage(wrapper)).toContain('de 7 vorige versies');
  });

  it('keeps a custom choice that could not be saved, says why under the options and retries on choosing it again', async () => {
    const wrapper = makeWrapper();
    await untilIdle();
    const working = backend.fetch;

    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('netwerk weg')));
    await choose(wrapper, 'retention-custom');

    expect(wrapper.find('[data-testid="retention-custom"]').attributes('checked')).toBeDefined();
    expect(field(wrapper).attributes('value')).toBe('5');
    const error = wrapper.find('[data-testid="retention-choice-error"]');
    expect(error.attributes('text')).toBe(
      'Niet opgeslagen. Controleer je verbinding en probeer het opnieuw.',
    );
    expect(error.attributes('variant')).toBe('alert');
    expect(error.element.parentElement?.getAttribute('role')).toBe('alert');
    expect(wrapper.find('nldd-notification').exists()).toBe(false);
    expect(usage(wrapper)).not.toContain('eigen instelling');

    vi.stubGlobal('fetch', working);
    await wrapper.find('[data-testid="retention-custom"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="retention-choice-error"]').exists()).toBe(false);
    expect(backend.data.sites[0]!.liveVersionsKept).toBe(5);
    expect(usage(wrapper)).toContain('Dit is een eigen instelling van deze site.');
  });

  it('keeps the default choice that could not be saved and retries on choosing it again', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();
    const working = backend.fetch;

    refuseWith({ title: 'Geen toegang', detail: 'Je rol is te smal.', code: 'INSUFFICIENT_ROLE' }, 403);
    await choose(wrapper, 'retention-default');

    expect(wrapper.find('[data-testid="retention-default"]').attributes('checked')).toBeDefined();
    expect(wrapper.find('[data-testid="retention-choice-error"]').attributes('text')).toBe(
      'Niet opgeslagen: Je rol is te smal. Probeer het opnieuw.',
    );
    expect(usage(wrapper)).toContain('de 3 vorige versies');

    vi.stubGlobal('fetch', working);
    await wrapper.find('[data-testid="retention-default"]').trigger('click');
    await untilIdle();

    expect(wrapper.find('[data-testid="retention-choice-error"]').exists()).toBe(false);
    expect(backend.data.sites[0]!.liveVersionsKept).toBeNull();
    expect(usage(wrapper)).toContain('de 5 vorige versies');
  });

  it('ignores a click on an option while no choice is waiting to be retried', async () => {
    const puts = countPuts();
    const wrapper = makeWrapper();
    await untilIdle();

    await wrapper.find('[data-testid="retention-default"]').trigger('click');
    await untilIdle();
    expect(puts.count()).toBe(0);

    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('netwerk weg')));
    await choose(wrapper, 'retention-custom');
    await wrapper.find('[data-testid="retention-default"]').trigger('click');
    await untilIdle();

    // A click on the other option is not a retry; its own change event chooses it.
    expect(wrapper.find('[data-testid="retention-custom"]').attributes('checked')).toBeDefined();
  });

  it('saves a number equal to the default as the own number after a failed custom choice', async () => {
    const wrapper = makeWrapper();
    await untilIdle();
    const working = backend.fetch;
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('netwerk weg')));
    await choose(wrapper, 'retention-custom');

    vi.stubGlobal('fetch', working);
    await commit(wrapper, '5');

    expect(backend.data.sites[0]!.liveVersionsKept).toBe(5);
  });

  it('reads a committed number from the field itself when the event carries no detail', async () => {
    backend.data.sites[0]!.liveVersionsKept = 3;
    const wrapper = makeWrapper();
    await untilIdle();

    const element = field(wrapper).element as HTMLInputElement;
    Object.defineProperty(element, 'value', { value: '9', configurable: true });
    element.dispatchEvent(new Event('change'));
    await untilIdle();

    expect(backend.data.sites[0]!.liveVersionsKept).toBe(9);
  });

  it('shows only the text to someone who is not a site admin', async () => {
    backend.data.loggedInMemberId = 'lid-3';
    backend.data.sites[0]!.liveVersionsKept = 3;

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="retention-box"]').exists()).toBe(false);
    expect(usage(wrapper)).toContain('de 3 vorige versies');
  });

  it('shows no choice when the session cannot be read', async () => {
    const inner = backend.fetch;
    vi.stubGlobal('fetch', (request: RequestInfo | URL, init?: RequestInit) =>
      String(request).endsWith('/me') ? Promise.reject(new TypeError('netwerk weg')) : inner(request, init),
    );

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="versions-storage"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="retention-box"]').exists()).toBe(false);
  });

  it('lets an admin through a site role alone, without a group role', async () => {
    backend.data.loggedInMemberId = 'lid-4';

    const wrapper = makeWrapper();
    await untilIdle();

    expect(wrapper.find('[data-testid="retention-choice"]').exists()).toBe(true);
  });
});
