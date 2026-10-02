/**
 * The release notes shipped with the SPA: `content/releases/<calver>.<nl|en>.md`.
 * At release time `unreleased.*` is renamed to the version it ships in (or
 * removed when empty), so only versioned files count here.
 */
import type { Locale } from './i18n';

export interface Release {
  /** CalVer without the leading `v`, e.g. `2026.10.1`. */
  version: string;
  /** The calendar day of the version, `YYYY-MM-DD`. */
  date: string;
  text: string;
}

const CALVER = /^\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)?$/;
const FILE = /(?:^|\/)(\d{4}\.\d{1,2}\.\d{1,2}(?:\.\d+)?)\.(nl|en)\.md$/;

const files = import.meta.glob('./content/releases/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

/** Numeric, part by part: 2026.10.1 is after 2026.9.30, 2026.9.27.2 after 2026.9.27. */
export function compareCalver(a: string, b: string): number {
  const left = a.split('.').map(Number);
  const right = b.split('.').map(Number);
  for (let i = 0; i < Math.max(left.length, right.length); i++) {
    const difference = (left[i] ?? 0) - (right[i] ?? 0);
    if (difference !== 0) return difference;
  }
  return 0;
}

export function calverDate(version: string): string {
  const [year, month, day] = version.split('.');
  return `${year}-${month!.padStart(2, '0')}-${day!.padStart(2, '0')}`;
}

/** The day (`YYYY-MM-DD`) a CalVer names; empty for anything else. */
export function versionDay(version: string): string {
  return CALVER.test(version) ? calverDate(version) : '';
}

/**
 * The id of a day's heading, and the hash that points at it. The `d` keeps it
 * from starting with a digit. Empty for a version that is not a CalVer, such as
 * `dev` or a git describe string.
 */
export function dayAnchor(version: string): string {
  const day = versionDay(version);
  return day ? `d${day}` : '';
}

export interface ReleaseDay {
  date: string;
  /** The texts of every release of the day, newest first. */
  texts: string[];
}

/** One entry per calendar day, in the order given: the intermediate versions are not told apart. */
export function groupByDate(releases: Release[]): ReleaseDay[] {
  const days: ReleaseDay[] = [];
  for (const release of releases) {
    const last = days.at(-1);
    if (last?.date === release.date) last.texts.push(release.text);
    else days.push({ date: release.date, texts: [release.text] });
  }
  return days;
}

/** Newest first; a release that has no text in `locale` is left out. */
export function loadReleases(locale: Locale, source: Record<string, string> = files): Release[] {
  const releases: Release[] = [];
  for (const [path, text] of Object.entries(source)) {
    const match = FILE.exec(path);
    if (match && match[2] === locale) {
      releases.push({ version: match[1]!, date: calverDate(match[1]!), text });
    }
  }
  return releases.sort((a, b) => compareCalver(b.version, a.version));
}
