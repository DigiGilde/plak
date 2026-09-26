/**
 * The handing out of the design system's own words. What is checked here is
 * the mechanism; that the table is complete and its Dutch matches the package
 * is `tests/design-system-text.test.ts`.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { nextTick } from 'vue';

import { _setLocaleForTest } from './index';
import { useDesignSystemText } from './designSystem';

interface Translated extends HTMLElement {
  translations?: Record<string, string>;
}

const OPEN_MENU = 'components.combo-box.open-menu-action';

let stop: (() => void) | null = null;
let root: HTMLElement | null = null;

function container(): HTMLElement {
  root = document.createElement('div');
  document.body.append(root);
  return root;
}

function comboBox(parent: ParentNode): Translated {
  const element = document.createElement('nldd-combo-box') as Translated;
  parent.append(element);
  return element;
}

afterEach(() => {
  stop?.();
  stop = null;
  root?.remove();
  root = null;
  _setLocaleForTest('nl');
});

describe('useDesignSystemText', () => {
  it('speaks to an element that is already on the page', () => {
    _setLocaleForTest('en');
    const parent = container();
    const field = comboBox(parent);

    stop = useDesignSystemText(parent);

    expect(field.translations?.[OPEN_MENU]).toBe('Show options');
  });

  it('speaks to an element that arrives later, and to what it brings', async () => {
    _setLocaleForTest('en');
    const parent = container();
    stop = useDesignSystemText(parent);

    const wrapper = document.createElement('div');
    const nested = comboBox(wrapper);
    parent.append(wrapper);
    const loose = comboBox(parent);
    await nextTick();

    expect(nested.translations?.[OPEN_MENU]).toBe('Show options');
    expect(loose.translations?.[OPEN_MENU]).toBe('Show options');
  });

  it('leaves an element it has no words for alone, and plain text with it', async () => {
    const parent = container();
    stop = useDesignSystemText(parent);

    const untouched = document.createElement('nldd-button') as Translated;
    parent.append(untouched, document.createTextNode('los'));
    await nextTick();

    expect(untouched.translations).toBeUndefined();
  });

  it('follows a change of language', async () => {
    const parent = container();
    const field = comboBox(parent);
    stop = useDesignSystemText(parent);
    expect(field.translations?.[OPEN_MENU]).toBe('Toon opties');

    _setLocaleForTest('en');
    await nextTick();

    expect(field.translations?.[OPEN_MENU]).toBe('Show options');
  });

  it('stops when it is told to', async () => {
    const parent = container();
    stop = useDesignSystemText(parent);
    stop();
    stop = null;

    const late = comboBox(parent);
    _setLocaleForTest('en');
    await nextTick();

    expect(late.translations).toBeUndefined();
  });
});
