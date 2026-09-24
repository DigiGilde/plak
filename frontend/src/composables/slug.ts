/**
 * Slug rules for sites and groups. SLUG_PATTERN mirrors constants.py so the
 * client rejects what the backend rejects, without trusting that blindly: the
 * server validates again.
 */

// The field gets the rule as a native `pattern` (which anchors itself), the
// validation item as `match` (which does not anchor, and lets the empty value
// through so that an empty slug only trips the required item). The hyphen is
// escaped because the browser compiles `pattern` with the v flag, where a bare
// `-` inside a character class is a syntax error.
export const SLUG_PATTERN = '[a-z0-9]([a-z0-9\\-]{0,61}[a-z0-9])?';
export const SLUG_RE = new RegExp(`^${SLUG_PATTERN}$`);
export const SLUG_MATCH = `^(${SLUG_PATTERN})?$`;

/**
 * What a hand-typed slug becomes while it is being typed: the same rules as
 * `slugify`, except that a hyphen at the start or the end survives. Stripping
 * it here would swallow the hyphen the next character is typed after, so
 * "mijn rapport" would come out as "mijnrapport". A leading or trailing hyphen
 * that is still there on submit is caught by the validation rules.
 */
export function slugifyTyped(value: string): string {
  return value
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .slice(0, 63);
}

export function slugify(value: string): string {
  return slugifyTyped(value).replace(/^-+|-+$/g, '');
}

/**
 * In-app path below a group, or undefined for a value that is not a slug. A
 * slug out of `route.params` is whatever the visitor's URL decoded to, and
 * `/` + `//evil.com` or `/` + `\evil.com` is a link off our own origin rather
 * than a path within the app.
 */
export function groupPath(slug: string, suffix = ''): string | undefined {
  return SLUG_RE.test(slug) ? `/${slug}${suffix}` : undefined;
}
