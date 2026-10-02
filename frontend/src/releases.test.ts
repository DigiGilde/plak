import { describe, expect, it } from 'vitest';

import { calverDate, compareCalver, dayAnchor, groupByDate, loadReleases, versionDay } from './releases';

const glob = (files: Record<string, string>) =>
  Object.fromEntries(
    Object.entries(files).map(([name, text]) => [`./content/releases/${name}`, text]),
  );

describe('compareCalver', () => {
  it('compares numerically, not as text', () => {
    expect(compareCalver('2026.10.1', '2026.9.30')).toBeGreaterThan(0);
    expect(compareCalver('2026.9.30', '2026.10.1')).toBeLessThan(0);
  });

  it('puts a same-day counter after the release without one', () => {
    expect(compareCalver('2026.9.27.2', '2026.9.27')).toBeGreaterThan(0);
    expect(compareCalver('2026.9.27', '2026.9.27.2')).toBeLessThan(0);
    expect(compareCalver('2026.9.27.2', '2026.9.27.10')).toBeLessThan(0);
  });

  it('calls equal versions equal', () => {
    expect(compareCalver('2026.9.27', '2026.9.27')).toBe(0);
  });
});

describe('calverDate', () => {
  it('derives a zero-padded day, ignoring the counter', () => {
    expect(calverDate('2026.9.7')).toBe('2026-09-07');
    expect(calverDate('2026.10.21.3')).toBe('2026-10-21');
  });
});

describe('loadReleases', () => {
  const source = glob({
    '2026.9.30.nl.md': 'nl 9.30',
    '2026.9.30.en.md': 'en 9.30',
    '2026.10.1.nl.md': 'nl 10.1',
    '2026.10.1.en.md': 'en 10.1',
    '2026.9.27.2.en.md': 'en 9.27.2',
    '2026.9.27.en.md': 'en 9.27',
    'unreleased.nl.md': 'nl next',
    'unreleased.en.md': 'en next',
    'notes.en.md': 'not a version',
    '2026.10.en.md': 'too short',
    '2026.10.1.fr.md': 'wrong language',
  });

  it('returns the requested language, newest first', () => {
    expect(loadReleases('en', source).map((r) => [r.version, r.text])).toEqual([
      ['2026.10.1', 'en 10.1'],
      ['2026.9.30', 'en 9.30'],
      ['2026.9.27.2', 'en 9.27.2'],
      ['2026.9.27', 'en 9.27'],
    ]);
    expect(loadReleases('nl', source).map((r) => r.text)).toEqual(['nl 10.1', 'nl 9.30']);
  });

  it('ignores unreleased and malformed file names', () => {
    const versions = loadReleases('nl', source).map((r) => r.version);
    expect(versions).not.toContain('unreleased');
    expect(versions).toHaveLength(2);
  });

  it('derives the date from the version', () => {
    expect(loadReleases('en', source)[0]).toMatchObject({ version: '2026.10.1', date: '2026-10-01' });
  });

  it('is empty without released notes', () => {
    expect(loadReleases('en', glob({ 'unreleased.en.md': 'x' }))).toEqual([]);
  });

  it('reads the notes bundled with the app by default', () => {
    expect(Array.isArray(loadReleases('en'))).toBe(true);
  });
});

describe('dayAnchor', () => {
  it('names the day of the version, padded and without a leading digit', () => {
    expect(dayAnchor('2026.9.30.1')).toBe('d2026-09-30');
    expect(dayAnchor('2026.10.2')).toBe('d2026-10-02');
  });

  it('is empty for a version that is not a CalVer', () => {
    expect(dayAnchor('dev')).toBe('');
    expect(dayAnchor('2026.9.30-5-g1a2b3c4')).toBe('');
  });
});

describe('groupByDate', () => {
  it('puts releases of one day under one date, keeping the order', () => {
    const release = (version: string) => ({ version, date: calverDate(version), text: version });
    const days = groupByDate([release('2026.10.1'), release('2026.9.30.1'), release('2026.9.30')]);

    expect(days.map((d) => [d.date, d.texts])).toEqual([
      ['2026-10-01', ['2026.10.1']],
      ['2026-09-30', ['2026.9.30.1', '2026.9.30']],
    ]);
  });

  it('is empty without releases', () => {
    expect(groupByDate([])).toEqual([]);
  });
});

describe('versionDay', () => {
  it('is the padded day of a CalVer and empty for anything else', () => {
    expect(versionDay('2026.9.30.1')).toBe('2026-09-30');
    expect(versionDay('dev')).toBe('');
    expect(versionDay('2026.9.30-5-g1a2b3c4')).toBe('');
  });
});
