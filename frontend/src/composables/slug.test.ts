import { describe, expect, it } from 'vitest';

import { groupPath, SLUG_RE, slugify, slugifyTyped } from './slug';

describe('slugify', () => {
  it.each([
    ['Mijn Rapport 2026', 'mijn-rapport-2026'],
    ['  spaties  ', 'spaties'],
    ['onder_streepjes', 'onder-streepjes'],
    ['Café Amsterdam', 'cafe-amsterdam'],
    ['a---b', 'a-b'],
    ['???', ''],
  ])('turns %s into the slug %s', (typed, expected) => {
    expect(slugify(typed)).toBe(expected);
  });

  it('cuts off at 63 characters and leaves no hyphen at the end', () => {
    const value = slugify(`${'a'.repeat(62)}-b`);
    expect(value.length).toBeLessThanOrEqual(63);
    expect(SLUG_RE.test(value)).toBe(true);
  });
});

describe('slugifyTyped (normalizing while typing)', () => {
  it('makes "Mijn Rapport 2026" come out letter by letter as mijn-rapport-2026', () => {
    // What a field does on every input event: normalise the whole value again.
    // Nothing may be reordered or swallowed, or the caret jumps.
    let field = '';
    for (const character of 'Mijn Rapport 2026') {
      field = slugifyTyped(field + character);
    }
    expect(field).toBe('mijn-rapport-2026');
  });

  it('keeps a hyphen at the start or end, so typing can continue after it', () => {
    expect(slugifyTyped('mijn ')).toBe('mijn-');
    expect(slugifyTyped('-begin')).toBe('-begin');
  });

  it('removes what is not allowed and folds repetitions together', () => {
    expect(slugifyTyped('Mijn!!! Site')).toBe('mijn-site');
    expect(slugifyTyped('a___b')).toBe('a-b');
  });
});

describe('groupPath', () => {
  it('builds the path of a group that the backend could have issued', () => {
    expect(groupPath('team-aurora')).toBe('/team-aurora');
    expect(groupPath('team-aurora', '/-/members')).toBe('/team-aurora/-/members');
  });

  it.each(['//example.com', '/\\example.com', '/\\/example.com', 'Nldd', 'a b', '-nldd'])(
    'gives no path for %s, so it can never become an off-origin href',
    (value) => {
      expect(groupPath(value)).toBeUndefined();
      expect(groupPath(value, '/-/members')).toBeUndefined();
    },
  );
});
