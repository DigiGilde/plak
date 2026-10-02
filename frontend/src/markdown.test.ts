import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { defineComponent } from 'vue';

import { isSafeHref, renderMarkdown } from './markdown';

function render(source: string, topLevel = 3): string {
  const Component = defineComponent({ render: () => renderMarkdown(source, topLevel) });
  return mount(Component).html();
}

describe('renderMarkdown blocks', () => {
  it('maps ## and ### to the levels below the page heading', () => {
    expect(render('## One\n\n### Two')).toBe('<h3>One</h3>\n<h4>Two</h4>');
    expect(render('## One\n\n### Two', 2)).toBe('<h2>One</h2>\n<h3>Two</h3>');
  });

  it('never goes deeper than h6', () => {
    expect(render('### Deep', 6)).toBe('<h6>Deep</h6>');
  });

  it('joins the hard-wrapped lines of a paragraph and splits on a blank line', () => {
    expect(render('first line\nsecond line\n\nnext')).toBe(
      '<p>first line second line</p>\n<p>next</p>',
    );
  });

  it('renders a bullet list, joining a wrapped item', () => {
    expect(render('- one\n- two\n  continued\n- three')).toBe(
      '<ul>\n  <li>one</li>\n  <li>two continued</li>\n  <li>three</li>\n</ul>',
    );
  });

  it('ends a paragraph where a list or a heading starts, and a list where a heading starts', () => {
    expect(render('text\n- item\n## Head')).toBe(
      '<p>text</p>\n<ul>\n  <li>item</li>\n</ul>\n<h3>Head</h3>',
    );
  });

  it('ends a list at a blank line and starts a paragraph after it', () => {
    expect(render('- item\n\nafter')).toBe('<ul>\n  <li>item</li>\n</ul>\n<p>after</p>');
  });

  it('renders nothing for an empty source', () => {
    expect(render('')).toBe('');
  });

  it('treats a heading level it does not support as paragraph text', () => {
    expect(render('# Title')).toBe('<p># Title</p>');
  });
});

describe('renderMarkdown inline', () => {
  it('renders code and bold, also inside a heading and a list item', () => {
    expect(render('Use `plak publish` and **mind** it')).toBe(
      '<p>Use <code>plak publish</code> and <strong>mind</strong> it</p>',
    );
    expect(render('## A `b`')).toBe('<h3>A <code>b</code></h3>');
    expect(render('- **x**')).toBe('<ul>\n  <li><strong>x</strong></li>\n</ul>');
  });

  it('keeps code and bold characters that are not markup', () => {
    expect(render('a * b ` c')).toBe('<p>a * b ` c</p>');
  });

  it('renders an https link with a safe rel', () => {
    expect(render('see [docs](https://example.org/a?b=1)')).toBe(
      '<p>see <a href="https://example.org/a?b=1" rel="noopener noreferrer">docs</a></p>',
    );
  });

  it('renders a same-origin path as a plain link', () => {
    expect(render('[about](/-/about)')).toBe('<p><a href="/-/about">about</a></p>');
  });

  it.each([
    'http://example.org',
    'javascript:alert(1)',
    'data:text/html,x',
    'mailto:a@b.nl',
    '//evil.example/x',
    '/\\evil.example',
    'relative/path',
    'https://',
  ])('leaves a link to %s as plain text', (href) => {
    const html = render(`[x](${href})`);
    expect(html).not.toContain('<a');
    expect(html).toContain('[x]');
  });
});

describe('isSafeHref', () => {
  it('accepts https and same-origin paths only', () => {
    expect(isSafeHref('https://example.org')).toBe(true);
    expect(isSafeHref('/-/about')).toBe(true);
    expect(isSafeHref('http://example.org')).toBe(false);
    expect(isSafeHref('//example.org')).toBe(false);
    expect(isSafeHref('not a url')).toBe(false);
  });
});
