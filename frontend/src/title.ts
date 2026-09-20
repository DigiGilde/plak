/**
 * The title of the browser tab.
 *
 * Without this, every route renders the same "Plak": a row of open tabs says
 * nothing, and a screen reader announces nothing about where a navigation
 * landed (WCAG 2.4.2). Parts go in from specific to general, the way a breadcrumb
 * reads backwards, so the distinguishing word is the one that survives a
 * narrow tab.
 */
const PRODUCT = 'Plak';

export function setDocumentTitle(...parts: (string | null | undefined)[]): void {
  const named = parts.filter((part): part is string => Boolean(part));
  // "Over Plak - Plak" says the name twice. An exact tail rather than a
  // contains-check, so a site called "Plakkaat" keeps its suffix.
  const last = named[named.length - 1];
  const doubled = last === PRODUCT || last?.endsWith(` ${PRODUCT}`);
  document.title = (doubled ? named : [...named, PRODUCT]).join(' - ');
}
