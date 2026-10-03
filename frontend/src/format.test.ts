import { describe, expect, it } from 'vitest';

import { contentUrl, formatBytes, formatCalendarDate, formatDate, siteUrl } from './format';

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
