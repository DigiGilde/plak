import { afterEach, describe, expect, it, vi } from 'vitest';

import { _setLocaleForTest } from './i18n';
import {
  contentUrl,
  formatBytes,
  formatCalendarDate,
  formatDate,
  lastRedirectDay,
  redirectDayFromToday,
  siteUrl,
} from './format';

describe('formatDate', () => {
  it('formats an ISO timestamp in the date style of the language on screen', () => {
    expect(formatDate('2026-01-15T10:00:00Z')).not.toBe('-');
  });

  it('falls back to a dash without a date, null or undefined alike', () => {
    expect(formatDate(null)).toBe('-');
    expect(formatDate(undefined)).toBe('-');
  });
});

describe('formatCalendarDate', () => {
  it('prints the day it names, whatever the time zone of the reader', () => {
    expect(formatCalendarDate('2026-10-01')).toBe('1 oktober 2026');
  });
});

describe('lastRedirectDay', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    _setLocaleForTest('nl');
  });

  // The API sends the moment the redirect is over: midnight in Amsterdam at
  // the start of the day after the last day it redirects.
  it.each([
    ['2026-11-06T23:00:00Z', '6 november 2026', 'midnight in winter time (UTC+1)'],
    ['2026-08-06T22:00:00Z', '6 augustus 2026', 'midnight in summer time (UTC+2)'],
    ['2026-10-25T23:00:00Z', '25 oktober 2026', 'the day the clocks went back, 25 hours long'],
    ['2026-03-29T22:00:00Z', '29 maart 2026', 'the day the clocks went forward, 23 hours long'],
  ])('names the day before %s: %s (%s)', (redirectsUntil, expected) => {
    expect(lastRedirectDay(redirectsUntil)).toBe(expected);
  });

  it('is still the last day a millisecond before midnight and no longer at midnight itself', () => {
    expect(lastRedirectDay('2026-11-06T23:00:00.001Z')).toBe('7 november 2026');
    expect(lastRedirectDay('2026-11-06T23:00:00.000Z')).toBe('6 november 2026');
    expect(lastRedirectDay('2026-11-06T22:59:59.999Z')).toBe('6 november 2026');
  });

  it('reads the day in Amsterdam, whatever the time zone of the reader', () => {
    vi.stubEnv('TZ', 'Pacific/Auckland');

    // 12:00 on the 7th in Auckland, but still the 6th in Amsterdam.
    expect(new Date('2026-11-06T23:00:00Z').getDate()).toBe(7);
    expect(lastRedirectDay('2026-11-06T23:00:00Z')).toBe('6 november 2026');
  });

  it('writes the date in the language on screen', () => {
    _setLocaleForTest('en');

    expect(lastRedirectDay('2026-11-06T23:00:00Z')).toBe('6 November 2026');
  });
});

describe('redirectDayFromToday', () => {
  afterEach(() => {
    _setLocaleForTest('nl');
  });

  it('is the 30th day after today', () => {
    expect(redirectDayFromToday(30, new Date('2026-10-08T10:00:00Z'))).toBe('7 november 2026');
  });

  it('takes today at the clock in Amsterdam: ten past midnight there is already the next day', () => {
    // 00:10 on the 8th in Amsterdam is still the 7th in UTC.
    expect(redirectDayFromToday(30, new Date('2026-10-07T22:10:00Z'))).toBe('7 november 2026');
    expect(redirectDayFromToday(30, new Date('2026-10-07T21:59:59Z'))).toBe('6 november 2026');
  });

  it('counts days on the calendar, not hours, when the clocks go back in between', () => {
    // 00:30 on the 25th in Amsterdam, the day the clocks go back: 30 times 24
    // hours later it is only the 24th of November there.
    expect(redirectDayFromToday(30, new Date('2026-10-24T22:30:00Z'))).toBe('24 november 2026');
  });

  it('counts days on the calendar, not hours, when the clocks go forward in between', () => {
    // 23:30 on the 28th of March in Amsterdam, the night the clocks go forward:
    // 30 times 24 hours later it is already the 28th of April there.
    expect(redirectDayFromToday(30, new Date('2026-03-28T22:30:00Z'))).toBe('27 april 2026');
  });

  it('crosses the end of a month and of a year', () => {
    expect(redirectDayFromToday(30, new Date('2026-12-15T12:00:00Z'))).toBe('14 januari 2027');
  });

  it('reads the number of days it is given, not a number of its own', () => {
    expect(redirectDayFromToday(7, new Date('2026-10-08T10:00:00Z'))).toBe('15 oktober 2026');
  });

  it('starts from today when no day is given', () => {
    const today = redirectDayFromToday(0);

    expect(today).toBe(formatCalendarDate(new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Amsterdam' })));
  });

  it('writes the date in the language on screen', () => {
    _setLocaleForTest('en');

    expect(redirectDayFromToday(30, new Date('2026-10-08T10:00:00Z'))).toBe('7 November 2026');
  });
});

describe('formatBytes', () => {
  it('counts small sizes in bytes without decimals', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(1023)).toBe('1.023 B');
  });

  it('steps up to KiB, MiB and GiB with at most one decimal', () => {
    expect(formatBytes(1024)).toBe('1 KiB');
    expect(formatBytes(1536)).toBe('1,5 KiB');
    expect(formatBytes(240 * 1024 * 1024)).toBe('240 MiB');
    expect(formatBytes(1024 ** 3)).toBe('1 GiB');
  });

  it('stays in GiB for anything larger', () => {
    expect(formatBytes(2048 * 1024 ** 3)).toBe('2.048 GiB');
  });
});

describe('contentUrl and siteUrl', () => {
  it('builds content paths on the given content origin', () => {
    expect(contentUrl('https://sites.plak.test', '/team-aurora/website/_preview/pr-42/')).toBe(
      'https://sites.plak.test/team-aurora/website/_preview/pr-42/',
    );
    expect(siteUrl('https://sites.plak.test', 'team-aurora', 'website')).toBe(
      'https://sites.plak.test/team-aurora/website/',
    );
  });

  it('is insensitive to a trailing slash on the base or a leading slash on the path', () => {
    expect(contentUrl('https://sites.plak.test/', '/team-aurora/website/')).toBe(
      'https://sites.plak.test/team-aurora/website/',
    );
    expect(contentUrl('https://sites.plak.test', 'team-aurora/website/')).toBe(
      'https://sites.plak.test/team-aurora/website/',
    );
  });

  it('never builds on the origin of the admin SPA itself', () => {
    // jsdom runs on https://plak.test/; the content host is a different origin.
    expect(siteUrl('https://sites.plak.test', 'team-aurora', 'website')).not.toContain(
      window.location.origin,
    );
  });
});
