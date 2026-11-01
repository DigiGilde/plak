/**
 * Every attribute on an nldd-* element in a template is one that element
 * declares. A design system upgrade renames attributes (`variant` became
 * `appearance` on buttons and lists, `label` became `accessible-label` on the
 * toolbar), and an element ignores a name it does not know without a word: the
 * button falls back to its default look, the toolbar loses its name. The
 * package's custom elements manifest says what each element takes, so the
 * check needs no list of its own to keep up to date.
 *
 * It reads the opening tags in the templates, the static and the bound
 * attributes alike. Event listeners and directives are skipped, and so are the
 * attributes every element takes (`slot`, `class`, `data-*`, `aria-*`, ...).
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const SRC = join(ROOT, 'src');

interface Declaration {
  tagName?: string;
  attributes?: { name: string }[];
  members?: { kind: string; name: string; attribute?: string }[];
}

const manifest = JSON.parse(
  readFileSync(join(ROOT, 'node_modules/@nldd/design-system/custom-elements.json'), 'utf8'),
) as { modules: { declarations?: Declaration[] }[] };

/** Per tag, the attribute names and the property names it declares. */
const DECLARED = new Map<string, Set<string>>();
for (const module of manifest.modules) {
  for (const declaration of module.declarations ?? []) {
    if (!declaration.tagName) continue;
    const names = new Set<string>();
    for (const attribute of declaration.attributes ?? []) names.add(attribute.name);
    for (const member of declaration.members ?? []) {
      if (member.kind === 'field') names.add(member.name);
      if (member.attribute) names.add(member.attribute);
    }
    DECLARED.set(declaration.tagName, names);
  }
}

/** HTML's global attributes, and what Vue itself consumes. */
const GLOBAL = new Set([
  'autofocus',
  'class',
  'dir',
  'hidden',
  'id',
  'inert',
  'is',
  'key',
  'lang',
  'popover',
  'ref',
  'role',
  'slot',
  'style',
  'tabindex',
  'title',
  'translate',
]);

/**
 * Attributes an element takes without declaring them, each with the reason.
 */
const UNDECLARED: Record<string, Record<string, string>> = {
  'nldd-text-field': {
    // nldd-validation-list reads `unmet` on its control, to name the items
    // only the server can judge; the control itself only carries it.
    unmet: 'read by nldd-validation-list, not by the field',
  },
};

function vueFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return vueFiles(path);
    return path.endsWith('.vue') ? [path] : [];
  });
}

// A quoted value may hold a `>` (v-if="count > 0"), so the tag runs to the
// first `>` outside quotes.
const TAG = /<(nldd-[a-z-]+)\b((?:[^>"']|"[^"]*"|'[^']*')*)>/g;
const ATTRIBUTE = /([:@#]?[A-Za-z][\w:.-]*)(?:\s*=\s*(?:"[^"]*"|'[^']*'))?/g;

function camel(name: string): string {
  return name.replace(/-([a-z])/g, (_, letter: string) => letter.toUpperCase());
}

function undeclaredAttributes(): string[] {
  const found: string[] = [];
  for (const path of vueFiles(SRC)) {
    const source = readFileSync(path, 'utf8');
    const template = source.slice(source.indexOf('<template>'));
    for (const tag of template.matchAll(TAG)) {
      const element = tag[1]!;
      const declared = DECLARED.get(element);
      if (!declared) {
        found.push(`${relative(ROOT, path)}: <${element}> is not in the design system`);
        continue;
      }
      for (const attribute of tag[2]!.matchAll(ATTRIBUTE)) {
        const written = attribute[1]!;
        if (/^(@|#|v-)/.test(written)) continue;
        const name = written.replace(/^(:|v-bind:)/, '').split('.')[0]!;
        if (name.startsWith('data-') || name.startsWith('aria-') || GLOBAL.has(name)) continue;
        if (declared.has(name) || declared.has(camel(name))) continue;
        if (UNDECLARED[element]?.[name]) continue;
        found.push(`${relative(ROOT, path)}: <${element} ${written}>`);
      }
    }
  }
  return found;
}

describe('Design system attributes', () => {
  it('reads the manifest of the installed design system', () => {
    // An empty map would let every attribute through.
    expect(DECLARED.get('nldd-button')?.has('appearance')).toBe(true);
    expect(DECLARED.get('nldd-banner')?.has('variant')).toBe(true);
  });

  it('puts on each nldd-* element only attributes it declares', () => {
    expect(undeclaredAttributes()).toEqual([]);
  });

  it('keeps every excused attribute undeclared, so none outlives its reason', () => {
    for (const [element, names] of Object.entries(UNDECLARED)) {
      for (const name of Object.keys(names)) {
        expect(DECLARED.get(element)?.has(name), `${element} ${name}`).toBe(false);
      }
    }
  });
});
