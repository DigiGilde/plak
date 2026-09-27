/**
 * jsdom lacks `window.matchMedia`; @nldd/design-system components use it (among
 * other things for breakpoint and `prefers-reduced-motion` detection) and crash
 * without this polyfill as soon as they are registered in a test.
 */
if (typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string): MediaQueryList =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}

/**
 * jsdom has no ResizeObserver; nldd-page (header height) and other layout
 * components instantiate one when they connect to the DOM.
 */
if (typeof window.ResizeObserver !== 'function') {
  window.ResizeObserver = class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
}

/**
 * jsdom has no usable CSS object; nldd-text-field calls
 * CSS.supports("width", ...) in its updated() lifecycle and otherwise crashes
 * with an unhandled rejection.
 */
{
  const g = globalThis as unknown as { CSS?: { supports?: (...a: string[]) => boolean } };
  if (typeof g.CSS !== 'object' || g.CSS === null) g.CSS = {};
  if (typeof g.CSS.supports !== 'function') g.CSS.supports = () => false;
}

/**
 * jsdom's attachInternals() returns an ElementInternals without
 * setFormValue/setValidity; form-associated NLDD components (nldd-text-field)
 * call those in their update lifecycle and otherwise throw an unhandled
 * rejection ("this._internals.setFormValue is not a function"), which stops the
 * test process with exit 1 despite passing asserts.
 */
const _attachInternals = HTMLElement.prototype.attachInternals;
HTMLElement.prototype.attachInternals = function (this: HTMLElement) {
  const internals =
    typeof _attachInternals === 'function'
      ? _attachInternals.call(this)
      : ({} as ElementInternals);
  const stub = internals as unknown as Record<string, unknown>;
  for (const name of ['setFormValue', 'setValidity', 'reportValidity', 'checkValidity']) {
    if (typeof stub[name] !== 'function') stub[name] = () => {};
  }
  return internals;
} as typeof HTMLElement.prototype.attachInternals;

/**
 * jsdom cannot parse modern selectors such as `:popover-open` and throws a
 * SyntaxError; NLDD components (nldd-tooltip) use that in their update
 * lifecycle. Catch only that unknown-selector error, so the rest of
 * matches()/querySelector() keeps working.
 */
for (const proto of [Element.prototype, Document.prototype] as const) {
  for (const method of ['matches', 'querySelector', 'querySelectorAll'] as const) {
    const original = (proto as unknown as Record<string, unknown>)[method];
    if (typeof original !== 'function') continue;
    (proto as unknown as Record<string, unknown>)[method] = function (
      this: unknown,
      ...args: unknown[]
    ) {
      try {
        return (original as (...a: unknown[]) => unknown).apply(this, args);
      } catch (error) {
        if (error instanceof DOMException && error.name === 'SyntaxError') {
          return method === 'querySelectorAll' ? [] : method === 'matches' ? false : null;
        }
        throw error;
      }
    };
  }
}

/**
 * Every test starts in Dutch.
 *
 * jsdom's navigator says `en-US`, so without this the whole suite would run in
 * the language a Dutch civil servant does not see, and an assertion on a label
 * would be testing the translation instead of the screen. A test about the
 * other language sets it for itself, and setting it back afterwards is that
 * test's own job.
 */
import { enableAutoUnmount } from '@vue/test-utils';
import { afterEach, beforeEach } from 'vitest';

import { _setLocaleForTest } from '../src/i18n';

beforeEach(() => {
  _setLocaleForTest('nl');
});

/**
 * Unmount whatever a test mounted, so component scopes are disposed of.
 *
 * Without this the debounce timer of memberSearch outlives its test and
 * fires after the environment is torn down, with a mock that no longer
 * returns a promise. That is an unhandled error which fails the whole run,
 * and only sometimes, which is the worst kind of red.
 */
enableAutoUnmount(afterEach);
