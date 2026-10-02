/**
 * The release notes' Markdown, rendered straight to Vue VNodes. No `v-html`
 * and no library: the subset is small and the output never passes through an
 * HTML string, so there is nothing to sanitise.
 *
 * Supported: `##` and `###` headings, paragraphs (hard-wrapped lines are
 * joined), `- ` lists, `code`, `**bold**` and links. A link survives only as
 * an `https:` URL or a same-origin path; everything else stays plain text.
 */
import { h, type VNode } from 'vue';

const HEADING = /^(#{2,3}) +(.+)$/;
const ITEM = /^- +(.*)$/;
const INLINE = /`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g;

/** `https:` or a path on this origin. `//host` and backslashes are not one. */
export function isSafeHref(href: string): boolean {
  if (href.startsWith('/')) {
    return !href.startsWith('//') && !href.includes('\\');
  }
  try {
    return new URL(href).protocol === 'https:';
  } catch {
    return false;
  }
}

function inline(text: string): (VNode | string)[] {
  const out: (VNode | string)[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const [whole, code, bold, label, href] = match;
    if (match.index > last) out.push(text.slice(last, match.index));
    last = match.index + whole.length;
    if (code !== undefined) {
      out.push(h('code', code));
    } else if (bold !== undefined) {
      out.push(h('strong', inline(bold)));
    } else if (isSafeHref(href!)) {
      out.push(
        href!.startsWith('/')
          ? h('a', { href }, label)
          : h('a', { href, rel: 'noopener noreferrer' }, label),
      );
    } else {
      out.push(whole);
    }
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/**
 * @param topLevel the heading level a `##` becomes, so the notes sit below
 * the page's own headings without skipping a level; `###` is one deeper.
 */
export function renderMarkdown(source: string, topLevel: number): VNode[] {
  const blocks: VNode[] = [];
  let paragraph: string[] = [];
  let items: string[] | null = null;

  const flush = (): void => {
    if (paragraph.length > 0) blocks.push(h('p', inline(paragraph.join(' '))));
    if (items) blocks.push(h('ul', items.map((item) => h('li', inline(item)))));
    paragraph = [];
    items = null;
  };

  for (const raw of source.split('\n')) {
    const line = raw.trim();
    const heading = HEADING.exec(line);
    const item = ITEM.exec(line);
    if (line === '') {
      flush();
    } else if (heading) {
      flush();
      const level = Math.min(6, topLevel + heading[1]!.length - 2);
      blocks.push(h(`h${level}`, inline(heading[2]!)));
    } else if (item) {
      if (!items) flush();
      items = [...(items ?? []), item[1]!];
    } else if (items) {
      // A wrapped continuation of the last bullet.
      items[items.length - 1] += ` ${line}`;
    } else {
      paragraph.push(line);
    }
  }
  flush();
  return blocks;
}
