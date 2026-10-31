import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import CodeSentence from './CodeSentence.vue';

/**
 * The sentence has no element of its own, so it is mounted in one: reading
 * the text of the wrapper of a component with several roots trims every piece.
 */
function sentence(text: string, codes: Record<string, string>): HTMLElement {
  const host = document.createElement('p');
  mount(CodeSentence, { props: { text, codes }, attachTo: host });
  return host;
}

describe('CodeSentence', () => {
  it('puts the code where the placeholder stands, in a code element that is not translated', () => {
    const host = sentence('Pas {site} aan naar {address}, dan werkt het.', {
      site: 'site:',
      address: 'groep/nieuw',
    });

    const codes = [...host.querySelectorAll('code')];
    expect(codes.map((code) => code.textContent)).toEqual(['site:', 'groep/nieuw']);
    expect(codes.every((code) => code.getAttribute('translate') === 'no')).toBe(true);
    expect(host.textContent).toBe('Pas site: aan naar groep/nieuw, dan werkt het.');
  });

  it('adds no space between the words and the code', () => {
    const host = sentence('({a})', { a: 'x' });

    expect(host.textContent).toBe('(x)');
  });

  it('leaves the order to the language', () => {
    const host = sentence('{second} komt voor {first}', { first: '1', second: '2' });

    expect(host.textContent).toBe('2 komt voor 1');
  });

  it('shows a sentence without placeholders as it is', () => {
    const host = sentence('Geen code.', {});

    expect(host.querySelector('code')).toBeNull();
    expect(host.textContent).toBe('Geen code.');
  });

  it('shows a placeholder at the very start or end of the sentence', () => {
    const host = sentence('{a} en {b}', { a: 'x', b: 'y' });

    expect(host.textContent).toBe('x en y');
  });
});
