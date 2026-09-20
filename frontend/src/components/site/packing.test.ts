import { describe, expect, it, vi } from 'vitest';

import {
  canPack,
  checkBundle,
  firstSecret,
  formatCount,
  formatSize,
  isDirectlyUsable,
  isMetadata,
  isSecret,
  makeTar,
  pack,
  readChosenFolder,
  readDrop,
  secretRefusal,
  type BundleFile,
} from './packing';
import { makeTransfer } from './testHelpers';

const BLOCK = 512;

function file(name: string, content = 'x'): File {
  return new File([content], name);
}

/** A file whose size is all that matters; the bytes do not really exist. */
function large(path: string, bytes: number): BundleFile {
  return { path, file: { name: path, size: bytes } as File };
}

function textIn(block: Uint8Array, start: number, length: number): string {
  return new TextDecoder()
    .decode(block.slice(start, start + length))
    .replace(/\0.*$/, '')
    .trim();
}

describe('packing: recognizing', () => {
  it('recognizes the metadata macOS and Windows send along', () => {
    expect(isMetadata('.DS_Store')).toBe(true);
    expect(isMetadata('__MACOSX')).toBe(true);
    expect(isMetadata('Thumbs.db')).toBe(true);
    expect(isMetadata('._index.html')).toBe(true);
    expect(isMetadata('index.html')).toBe(false);
  });

  it('recognizes what the API can handle without packing', () => {
    expect(isDirectlyUsable('site.zip')).toBe(true);
    expect(isDirectlyUsable('site.TAR.GZ')).toBe(true);
    expect(isDirectlyUsable('site.tgz')).toBe(true);
    expect(isDirectlyUsable('index.html')).toBe(true);
    expect(isDirectlyUsable('stijl.css')).toBe(false);
    expect(isDirectlyUsable('site.tar')).toBe(false);
  });

  it('formats counts and sizes the way a Dutch reader reads them', () => {
    expect(formatCount(1000)).toBe('1.000');
    expect(formatSize(12)).toBe('12 B');
    expect(formatSize(40 * 1024)).toBe('40 kB');
    expect(formatSize(1.5 * 1024 * 1024)).toBe('1,5 MB');
    expect(formatSize(100 * 1024 * 1024)).toBe('100 MB');
  });
});

describe('packing: reading a drop', () => {
  it('puts the contents of a dropped folder at the root and leaves metadata behind', async () => {
    const result = await readDrop(
      makeTransfer({
        'mijn-site': {
          'index.html': file('index.html'),
          '.DS_Store': file('.DS_Store'),
          __MACOSX: { '._index.html': file('._index.html') },
          assets: { 'stijl.css': file('stijl.css'), 'logo.svg': file('logo.svg') },
        },
      }),
    );

    expect(result.kind).toBe('bundle');
    if (result.kind !== 'bundle') return;
    expect(result.name).toBe('mijn-site');
    expect(result.files.map((item) => item.path).sort()).toEqual([
      'assets/logo.svg',
      'assets/stijl.css',
      'index.html',
    ]);
  });

  it('passes a dropped archive through unchanged', async () => {
    const archive = file('dist.zip');
    const result = await readDrop(makeTransfer({ 'dist.zip': archive }));

    expect(result).toEqual({ kind: 'file', file: archive });
  });

  it('refuses a single file that cannot be turned into a site, with the rule included', async () => {
    const result = await readDrop(makeTransfer({ 'stijl.css': file('stijl.css') }));

    expect(result.kind).toBe('refusal');
    if (result.kind !== 'refusal') return;
    expect(result.reason).toContain('stijl.css');
    expect(result.reason).toContain('.zip');
  });

  it('bundles multiple dropped entries under one name', async () => {
    const result = await readDrop(
      makeTransfer({
        'index.html': file('index.html'),
        assets: { 'stijl.css': file('stijl.css') },
      }),
    );

    expect(result.kind).toBe('bundle');
    if (result.kind !== 'bundle') return;
    expect(result.name).toBe('site');
    expect(result.files.map((item) => item.path).sort()).toEqual([
      'assets/stijl.css',
      'index.html',
    ]);
  });

  it('stops reading once there are more files than would ever be accepted', async () => {
    const content: Record<string, File> = {};
    for (let i = 0; i < 1200; i += 1) {
      content[`bestand-${i}.html`] = file(`bestand-${i}.html`);
    }
    const result = await readDrop(makeTransfer({ site: content }));

    expect(result.kind).toBe('bundle');
    if (result.kind !== 'bundle') return;
    // One over the limit is enough to refuse on.
    expect(result.files.length).toBe(1001);
    expect(checkBundle(result.name, result.files)).toContain('meer dan 1.000');
  });

  it('falls back to loose files when the browser does not supply folders', async () => {
    const page = file('index.html');
    const result = await readDrop(
      makeTransfer({ 'index.html': page }, { entries: false }),
    );

    expect(result).toEqual({ kind: 'file', file: page });
  });

  it('bundles multiple loose files without folder support', async () => {
    const result = await readDrop(
      makeTransfer(
        { 'index.html': file('index.html'), 'stijl.css': file('stijl.css') },
        { entries: false },
      ),
    );

    expect(result).toMatchObject({ kind: 'bundle', name: 'site' });
  });

  it('names the bundle after the only file present', async () => {
    const result = await readDrop(
      makeTransfer({ 'stijl.css': file('stijl.css') }, { entries: false }),
    );

    expect(result).toMatchObject({ kind: 'bundle', name: 'stijl.css' });
  });

  it('refuses a drop without usable content', async () => {
    const empty = { items: undefined, files: undefined } as unknown as DataTransfer;
    await expect(readDrop(empty)).resolves.toMatchObject({ kind: 'refusal' });

    const onlyMetadata = makeTransfer({ '.DS_Store': file('.DS_Store') }, { entries: false });
    await expect(readDrop(onlyMetadata)).resolves.toMatchObject({ kind: 'refusal' });
  });

  it('passes a read error through instead of swallowing it', async () => {
    const broken = {
      items: [
        {
          webkitGetAsEntry: () => ({
            name: 'kapot.html',
            isFile: true,
            isDirectory: false,
            file: (_resolve: unknown, reject: (error: Error) => void) =>
              reject(new Error('onleesbaar')),
          }),
        },
      ],
    } as unknown as DataTransfer;

    await expect(readDrop(broken)).rejects.toThrow('onleesbaar');
  });

  it('passes through an error reading a folder', async () => {
    const brokenFolder = {
      items: [
        {
          webkitGetAsEntry: () => ({
            name: 'map',
            isFile: false,
            isDirectory: true,
            createReader: () => ({
              readEntries: (_resolve: unknown, reject: (error: Error) => void) =>
                reject(new Error('map onleesbaar')),
            }),
          }),
        },
        { webkitGetAsEntry: () => null },
      ],
    } as unknown as DataTransfer;

    await expect(readDrop(brokenFolder)).rejects.toThrow('map onleesbaar');
  });
});

describe('packing: checks before sending', () => {
  it('lets a bundle with index.html at the root through', () => {
    expect(checkBundle('site', [{ path: 'index.html', file: file('index.html') }])).toBeNull();
  });

  it('peels off one wrapping folder, same as the ingest', () => {
    const files = [
      { path: 'dist/index.html', file: file('index.html') },
      { path: 'dist/stijl.css', file: file('stijl.css') },
    ];

    expect(checkBundle('mijn-site', files)).toBeNull();
  });

  it('refuses an empty bundle', () => {
    expect(checkBundle('site', [])).toContain('niets dat gepubliceerd kan worden');
  });

  it('refuses too many files, with the number included', () => {
    const files = Array.from({ length: 1001 }, (_, i) => large(`b-${i}.html`, 1));

    expect(checkBundle('site', files)).toContain('meer dan 1.000 bestanden');
  });

  it('refuses a folder structure that is too deep', () => {
    const deep = `${'map/'.repeat(10)}index.html`;

    expect(checkBundle('site', [large(deep, 1)])).toContain('dieper dan 10 mappen');
  });

  it('refuses a path that does not fit in a tar header', () => {
    const longName = `${'n'.repeat(120)}.html`;

    expect(checkBundle('site', [large(longName, 1)])).toContain('te lang voor een archief');
  });

  it('refuses a file that is too large, with its size included', () => {
    const files = [large('index.html', 1), large('video.mp4', 120 * 1024 * 1024)];

    expect(checkBundle('site', files)).toContain('"video.mp4" is 120 MB');
  });

  it('refuses a bundle that is too large in total', () => {
    const files = [
      large('index.html', 1),
      large('a.bin', 90 * 1024 * 1024),
      large('b.bin', 90 * 1024 * 1024),
      large('c.bin', 90 * 1024 * 1024),
      large('d.bin', 90 * 1024 * 1024),
      large('e.bin', 90 * 1024 * 1024),
      large('f.bin', 90 * 1024 * 1024),
    ];

    expect(checkBundle('site', files)).toContain('hoogstens 500 MB');
  });

  it('points out the capital letter in the homepage filename', () => {
    const files = [large('Index.html', 1), large('stijl.css', 1)];

    expect(checkBundle('site', files)).toContain('met kleine letters');
  });

  it('names the folder where the homepage does live', () => {
    const files = [large('lees-mij.txt', 1), large('dist/index.html', 1)];

    expect(checkBundle('mijn-site', files)).toContain('Sleep de map "dist" zelf');
  });

  it('says plainly when there is no homepage anywhere', () => {
    const notice = checkBundle('mijn-site', [large('stijl.css', 1)]);

    expect(notice).toContain('geen index.html in "mijn-site"');
  });
});

describe('packing: packing', () => {
  it('reports honestly whether the browser can pack', () => {
    expect(canPack()).toBe(true);

    vi.stubGlobal('CompressionStream', undefined);
    expect(canPack()).toBe(false);
    vi.unstubAllGlobals();
  });

  it('writes a readable tar: header, content, padding and closing', async () => {
    const tar = await makeTar([
      { path: 'index.html', file: file('index.html', '<h1>hallo</h1>') },
      { path: 'leeg.txt', file: file('leeg.txt', '') },
    ]);

    expect(tar.length % BLOCK).toBe(0);
    expect(textIn(tar, 0, 100)).toBe('index.html');
    expect(textIn(tar, 124, 12)).toBe('00000000016'); // 14 bytes, octal
    expect(textIn(tar, 257, 6)).toBe('ustar');
    expect(tar[156]).toBe(48); // type flag '0'
    expect(new TextDecoder().decode(tar.slice(BLOCK, BLOCK + 14))).toBe('<h1>hallo</h1>');
    // A header plus padded content, then a header without content for the
    // empty file, and two zero blocks to close.
    expect(tar.length).toBe(5 * BLOCK);
    expect(tar.slice(3 * BLOCK).every((byte) => byte === 0)).toBe(true);
  });

  it('calculates the header checksum the way tar expects it', async () => {
    const tar = await makeTar([{ path: 'index.html', file: file('index.html') }]);
    const stored = Number.parseInt(textIn(tar, 148, 8), 8);

    const header = tar.slice(0, BLOCK);
    header.fill(32, 148, 156);
    const calculated = header.reduce((sum, byte) => sum + byte, 0);

    expect(stored).toBe(calculated);
  });

  it("splits a long path across ustar's name and prefix fields", async () => {
    const folder = 'a'.repeat(50);
    const path = `${folder}/${folder}/index.html`;
    const tar = await makeTar([{ path, file: file('index.html') }]);

    // ustar reads the path as prefix + "/" + name.
    expect(textIn(tar, 345, 155)).toBe(folder);
    expect(textIn(tar, 0, 100)).toBe(`${folder}/index.html`);
  });

  it('reports progress per file', async () => {
    const steps: Array<[number, number]> = [];
    await makeTar(
      [
        { path: 'index.html', file: file('index.html') },
        { path: 'stijl.css', file: file('stijl.css') },
      ],
      (done, total) => steps.push([done, total]),
    );

    expect(steps).toEqual([
      [1, 2],
      [2, 2],
    ]);
  });

  it('passes an unreadable file through as an error instead of as empty content', async () => {
    vi.stubGlobal(
      'FileReader',
      class {
        onload: (() => void) | null = null;
        onerror: (() => void) | null = null;
        error: DOMException | null = null;
        readAsArrayBuffer(): void {
          setTimeout(() => this.onerror?.(), 0);
        }
      },
    );

    await expect(
      makeTar([{ path: 'index.html', file: file('index.html') }]),
    ).rejects.toThrow('file could not be read');

    vi.unstubAllGlobals();
  });

  it('produces a gzip archive with a name the ingest recognizes', async () => {
    const archive = await pack('Mijn Site!', [
      { path: 'index.html', file: file('index.html') },
    ]);

    expect(archive.name).toBe('Mijn-Site.tar.gz');
    expect(archive.type).toBe('application/gzip');
    const bytes = new Uint8Array(
      await new Promise<ArrayBuffer>((resolve) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result as ArrayBuffer);
        reader.readAsArrayBuffer(archive);
      }),
    );
    // Gzip magic: 1f 8b 08.
    expect([bytes[0], bytes[1], bytes[2]]).toEqual([31, 139, 8]);
  });

  it('falls back to a usable name when nothing is left of the folder name', async () => {
    const archive = await pack('...', [{ path: 'index.html', file: file('index.html') }]);

    expect(archive.name).toBe('site.tar.gz');
  });
});

describe('secrets in a bundle', () => {
  function bundleFile(path: string): BundleFile {
    return { path, file: new File(['x'], path.split('/').pop() ?? path) };
  }

  it.each(['.env', '.env.local', '.env.production', '.env.example'])(
    'recognizes %s as a secret',
    (name) => {
      expect(isSecret(name)).toBe(true);
    },
  );

  it.each(['.gitignore', '.well-known', 'environment.js', '.environment.html'])(
    'leaves %s alone',
    (name) => {
      expect(isSecret(name)).toBe(false);
    },
  );

  it('finds a .git at any depth', () => {
    expect(firstSecret([bundleFile('index.html'), bundleFile('docs/.git/config')])).toBe(
      'docs/.git/config',
    );
  });

  it('returns null when nothing is wrong', () => {
    expect(firstSecret([bundleFile('index.html'), bundleFile('.well-known/x')])).toBeNull();
  });

  it('says in the refusal what to do instead', () => {
    const refusal = secretRefusal('.env');
    expect(refusal.kind).toBe('refusal');
    expect(refusal.kind === 'refusal' && refusal.reason).toContain('dist');
  });
});

describe('a folder from the file picker', () => {
  function chosen(paths: string[]): File[] {
    return paths.map((path) => {
      const file = new File(['x'], path.split('/').pop() ?? path);
      Object.defineProperty(file, 'webkitRelativePath', { value: path });
      return file;
    });
  }

  it('strips the chosen folder from the paths, so the content lands at the root', () => {
    const outcome = readChosenFolder(chosen(['mijnsite/index.html', 'mijnsite/assets/stijl.css']));
    expect(outcome.kind).toBe('bundle');
    if (outcome.kind !== 'bundle') return;
    expect(outcome.name).toBe('mijnsite');
    expect(outcome.files.map((b) => b.path)).toEqual(['index.html', 'assets/stijl.css']);
  });

  it('leaves out metadata from the operating system', () => {
    const outcome = readChosenFolder(chosen(['site/index.html', 'site/.DS_Store', 'site/._x']));
    expect(outcome.kind === 'bundle' && outcome.files.map((b) => b.path)).toEqual([
      'index.html',
    ]);
  });

  it('refuses a project folder with a .env', () => {
    const outcome = readChosenFolder(chosen(['site/index.html', 'site/.env']));
    expect(outcome.kind).toBe('refusal');
  });

  it('refuses a folder with nothing publishable in it', () => {
    const outcome = readChosenFolder(chosen(['site/.DS_Store']));
    expect(outcome.kind).toBe('refusal');
  });
});
