/**
 * Turning dropped input into something the deploy API accepts.
 *
 * The API wants an archive (`.zip`, `.tar.gz`, `.tgz`) or a single `.html`
 * file; when a folder is dropped the browser hands over a tree of individual
 * `File` objects. That gap is closed here, on the browser side, so the API
 * contract stays untouched: read it out with `webkitGetAsEntry`, filter out the
 * macOS and Windows metadata, check it against the ingest limits, and pack it
 * as tar plus gzip (`CompressionStream`), without an extra dependency.
 *
 * The refusal deliberately comes before sending: the user learns why something
 * cannot work before a single byte goes over the wire.
 */
import { formatNumber } from '@/format';
import { t } from '@/i18n';

/**
 * Mirrors the ingest defaults (`backend/src/plak/config.py`:
 * `ingest_max_files`, `ingest_max_depth`, `ingest_max_file`,
 * `ingest_max_total`). They appear in no API response, so this is a copy; if
 * the server is configured more strictly it refuses anyway, to the same effect.
 */
export const LIMITS = {
  files: 1000,
  depth: 10,
  file: 100 * 1024 * 1024,
  total: 500 * 1024 * 1024,
} as const;

/** Name of the start page, as the ingest expects it in the root. */
const INDEX = 'index.html';

/** A file inside an archive still to be built. */
export interface BundleFile {
  /** Path inside the archive, `/`-separated, without a leading slash. */
  path: string;
  file: File;
}

export type DropResult =
  /** A single file the API already handles; forward it unchanged. */
  | { kind: 'file'; file: File }
  /** A folder or several files; has to be packed first. */
  | { kind: 'bundle'; name: string; files: BundleFile[] }
  /** Nothing can be made of this; `reason` is meant for the user. */
  | { kind: 'refusal'; reason: string };

const ARCHIVE_OR_PAGE = /\.(zip|tgz|tar\.gz|html)$/i;

/** What the deploy API accepts without packing (`ingest/unpacker.py`). */
export function isDirectlyUsable(name: string): boolean {
  return ARCHIVE_OR_PAGE.test(name);
}

const METADATA = new Set(['.DS_Store', '__MACOSX', 'Thumbs.db']);

/**
 * Operating-system bookkeeping, not site content. `__MACOSX` is the folder
 * Finder puts next to a folder inside a zip: a second root name that would
 * otherwise be packed and published alongside the site.
 */
export function isMetadata(name: string): boolean {
  return METADATA.has(name) || name.startsWith('._');
}

/**
 * Files that never belong on a website and that come along automatically with a
 * whole site folder. The ingest refuses them too (`SECRET_FILE` in
 * `ingest/unpacker.py`); they are here to say so before anything goes over the
 * wire, and to tell the user what to do instead.
 *
 * No blanket ban on dotfiles: `.well-known/` and `.gitignore` are allowed.
 */
const SECRET_FOLDER = '.git';
const SECRET_FILE = '.env';

export function isSecret(name: string): boolean {
  return name === SECRET_FOLDER || name === SECRET_FILE || name.startsWith(`${SECRET_FILE}.`);
}

/** The first path in the bundle that must not be published, or null. */
export function firstSecret(files: BundleFile[]): string | null {
  for (const { path } of files) {
    if (path.split('/').some(isSecret)) return path;
  }
  return null;
}

export function secretRefusal(path: string): DropResult {
  return { kind: 'refusal', reason: t('publish.packing.secret', { path }) };
}

// -- Reading a drop -----------------------------------------------------------

function entriesOf(transfer: DataTransfer): FileSystemEntry[] {
  const items = Array.from(transfer.items ?? []);
  return items
    .map((item) => (typeof item.webkitGetAsEntry === 'function' ? item.webkitGetAsEntry() : null))
    .filter((entry): entry is FileSystemEntry => entry !== null);
}

function readFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

/**
 * `readEntries` hands back one batch per call and is only done once it returns
 * an empty list; a single call therefore silently misses the rest of a large
 * folder.
 */
function readFolder(folder: FileSystemDirectoryEntry): Promise<FileSystemEntry[]> {
  const reader = folder.createReader();
  const all: FileSystemEntry[] = [];
  return new Promise((resolve, reject) => {
    const next = (): void => {
      reader.readEntries((batch) => {
        if (batch.length === 0) {
          resolve(all);
          return;
        }
        all.push(...batch);
        next();
      }, reject);
    };
    next();
  });
}

async function walkEntry(
  entry: FileSystemEntry,
  prefix: string,
  out: BundleFile[],
): Promise<void> {
  if (isMetadata(entry.name)) return;
  // One file over the limit is enough to refuse on; reading on through a
  // folder with tens of thousands of files only costs waiting time.
  if (out.length > LIMITS.files) return;
  const path = prefix === '' ? entry.name : `${prefix}/${entry.name}`;
  if (entry.isFile) {
    out.push({ path, file: await readFile(entry as FileSystemFileEntry) });
    return;
  }
  await readContents(entry as FileSystemDirectoryEntry, path, out);
}

async function readContents(
  folder: FileSystemDirectoryEntry,
  prefix: string,
  out: BundleFile[],
): Promise<void> {
  for (const child of await readFolder(folder)) {
    await walkEntry(child, prefix, out);
  }
}

function fromLooseFiles(loose: File[]): DropResult {
  const usable = loose.filter((file) => !isMetadata(file.name));
  if (usable.length === 0) {
    return { kind: 'refusal', reason: t('publish.packing.nothing') };
  }
  if (usable.length === 1 && isDirectlyUsable(usable[0]!.name)) {
    return { kind: 'file', file: usable[0]! };
  }
  return {
    kind: 'bundle',
    name: usable.length === 1 ? usable[0]!.name : 'site',
    files: usable.map((file) => ({ path: file.name, file })),
  };
}

/**
 * Reads what was dropped. A single folder yields its contents at the root of
 * the archive, because that is what the ingest expects as a site.
 */
export async function readDrop(transfer: DataTransfer): Promise<DropResult> {
  const entries = entriesOf(transfer);
  if (entries.length === 0) {
    // No `webkitGetAsEntry` (or a drop without files): then there is at most
    // a flat list of files, and so never a folder.
    return fromLooseFiles(Array.from(transfer.files ?? []));
  }

  if (entries.length === 1 && entries[0]!.isFile) {
    const file = await readFile(entries[0] as FileSystemFileEntry);
    if (isDirectlyUsable(file.name)) {
      return { kind: 'file', file };
    }
    return { kind: 'refusal', reason: t('publish.packing.notASite', { name: file.name }) };
  }

  const files: BundleFile[] = [];
  let name = 'site';
  if (entries.length === 1) {
    const folder = entries[0] as FileSystemDirectoryEntry;
    await readContents(folder, '', files);
    name = folder.name;
  } else {
    for (const entry of entries) {
      await walkEntry(entry, '', files);
    }
  }
  const secret = firstSecret(files);
  if (secret !== null) return secretRefusal(secret);
  return { kind: 'bundle', name, files };
}

/**
 * The same, but for a folder picked through the file chooser
 * (`webkitdirectory`). The browser then hands back a flat list in which every
 * file carries its path in `webkitRelativePath`, with the chosen folder as the
 * first segment. That first segment is dropped, because the ingest expects the
 * folder's contents at the root.
 */
export function readChosenFolder(chosen: File[]): DropResult {
  const files: BundleFile[] = [];
  let name = 'site';
  for (const file of chosen) {
    const parts = (file.webkitRelativePath || file.name).split('/');
    if (parts.length > 1) name = parts[0]!;
    const rest = parts.length > 1 ? parts.slice(1) : parts;
    if (rest.some(isMetadata)) continue;
    files.push({ path: rest.join('/'), file });
  }
  if (files.length === 0) {
    return { kind: 'refusal', reason: t('publish.packing.folderEmpty') };
  }
  const secret = firstSecret(files);
  if (secret !== null) return secretRefusal(secret);
  return { kind: 'bundle', name, files };
}

// -- Checks before sending -----------------------------------------------------

/** A count in the grouping conventions of the language on screen. */
export function formatCount(value: number): string {
  return formatNumber(value);
}

/**
 * A size in the unit that means something to the user. The limits are in
 * megabytes, a single page is not: "0 MB" next to a file that is definitely
 * there reads as a bug.
 */
export function formatSize(bytes: number): string {
  if (bytes < 1024) {
    return `${formatNumber(bytes)} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${formatNumber(Math.round(bytes / 1024))} kB`;
  }
  const mega = bytes / (1024 * 1024);
  return `${formatNumber(mega, { maximumFractionDigits: mega < 10 ? 1 : 0 })} MB`;
}

const count = formatCount;
const mb = formatSize;

/**
 * The same peeling as the ingest: as long as the root holds exactly one entry
 * and that entry is a folder, that folder becomes the root. Without this step a
 * dropped folder containing only a `dist/` would be refused here while the
 * server accepts it happily.
 */
function peeled(paths: string[]): string[] {
  let current = paths;
  for (;;) {
    const roots = new Set(current.map((path) => path.split('/')[0]!));
    if (roots.size !== 1) return current;
    const only = [...roots][0]!;
    if (current.includes(only)) return current;
    current = current.map((path) => path.slice(only.length + 1));
  }
}

/**
 * Says what is wrong with the bundle, or `null` when it can be sent. The order
 * follows the cost: first what makes the archive impossible, only then what
 * makes the site unusable.
 */
export function checkBundle(name: string, files: BundleFile[]): string | null {
  if (files.length === 0) {
    return t('publish.packing.bundleEmpty', { name });
  }
  if (files.length > LIMITS.files) {
    return t('publish.packing.tooManyFiles', { name, count: count(LIMITS.files) });
  }
  const tooDeep = files.find((item) => item.path.split('/').length > LIMITS.depth);
  if (tooDeep) {
    return t('publish.packing.tooDeep', { path: tooDeep.path, depth: count(LIMITS.depth) });
  }
  const tooLong = files.find((item) => !pathFits(item.path));
  if (tooLong) {
    return t('publish.packing.pathTooLong', { path: tooLong.path });
  }
  const tooLarge = files.find((item) => item.file.size > LIMITS.file);
  if (tooLarge) {
    return t('publish.packing.fileTooLarge', {
      path: tooLarge.path,
      size: mb(tooLarge.file.size),
      max: mb(LIMITS.file),
    });
  }
  const total = files.reduce((sum, item) => sum + item.file.size, 0);
  if (total > LIMITS.total) {
    return t('publish.packing.totalTooLarge', { name, size: mb(total), max: mb(LIMITS.total) });
  }

  const paths = peeled(files.map((item) => item.path));
  if (paths.includes(INDEX)) return null;
  const variant = paths.find((path) => path.toLowerCase() === INDEX);
  if (variant) {
    return t('publish.packing.indexCase', { found: variant, index: INDEX });
  }
  const candidate = paths
    .filter((path) => path.toLowerCase().endsWith(`/${INDEX}`))
    .sort((a, b) => a.split('/').length - b.split('/').length || a.localeCompare(b))[0];
  if (candidate) {
    const folder = candidate.slice(0, candidate.lastIndexOf('/'));
    return t('publish.packing.indexInFolder', {
      index: INDEX,
      name,
      path: candidate,
      folder,
    });
  }
  return t('publish.packing.indexMissing', { index: INDEX, name });
}

// -- Packing -------------------------------------------------------------------

/**
 * Gzipping uses `CompressionStream`, which older browsers lack. Without gzip
 * the browser cannot build an archive and a hand-made zip is the only way, so
 * the user has to be told.
 */
export function canPack(): boolean {
  return typeof globalThis.CompressionStream === 'function';
}

const BLOCK = 512;
const encoder = new TextEncoder();

/**
 * Explicitly over a plain `ArrayBuffer`: since TypeScript 5.7 the bare
 * `Uint8Array` also allows a `SharedArrayBuffer`, and `Blob` and
 * `WritableStream` do not accept that.
 */
type Bytes = Uint8Array<ArrayBuffer>;

function pathFits(path: string): boolean {
  try {
    splitPath(path);
    return true;
  } catch {
    return false;
  }
}

/**
 * ustar keeps a path in two fields: `name` (100 bytes) and `prefix`
 * (155 bytes), split on a `/`. Whatever fits in neither cannot be stored.
 */
function splitPath(path: string): { name: string; prefix: string } {
  if (encoder.encode(path).length <= 100) {
    return { name: path, prefix: '' };
  }
  for (let i = path.indexOf('/'); i !== -1; i = path.indexOf('/', i + 1)) {
    const prefix = path.slice(0, i);
    const name = path.slice(i + 1);
    if (encoder.encode(prefix).length <= 155 && encoder.encode(name).length <= 100) {
      return { name, prefix };
    }
  }
  throw new RangeError(`path does not fit in a tar header: ${path}`);
}

function writeText(block: Uint8Array, position: number, value: string): void {
  block.set(encoder.encode(value), position);
}

function octal(value: number, length: number): string {
  return `${value.toString(8).padStart(length - 1, '0')}\0`;
}

function header(path: string, size: number, timestamp: number): Bytes {
  const { name, prefix } = splitPath(path);
  const block = new Uint8Array(BLOCK);
  writeText(block, 0, name);
  writeText(block, 100, '0000644\0');
  writeText(block, 108, '0000000\0');
  writeText(block, 116, '0000000\0');
  writeText(block, 124, octal(size, 12));
  writeText(block, 136, octal(timestamp, 12));
  // The checksum field counts as eight spaces; the sum goes in afterwards.
  block.fill(32, 148, 156);
  block[156] = 48; // type flag '0': a regular file
  writeText(block, 257, 'ustar\0');
  writeText(block, 263, '00');
  writeText(block, 345, prefix);
  const sum = block.reduce((total, byte) => total + byte, 0);
  writeText(block, 148, `${sum.toString(8).padStart(6, '0')}\0 `);
  return block;
}

function padding(size: number): Bytes {
  return new Uint8Array((BLOCK - (size % BLOCK)) % BLOCK);
}

function concat(parts: Bytes[]): Bytes {
  const whole = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let position = 0;
  for (const part of parts) {
    whole.set(part, position);
    position += part.length;
  }
  return whole;
}

/**
 * FileReader rather than `Blob.arrayBuffer()`: that method is missing from
 * jsdom's Blob implementation, which is where this code is tested, while
 * FileReader works everywhere.
 */
function readBytes(file: File): Promise<Bytes> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(new Uint8Array(reader.result as ArrayBuffer));
    reader.onerror = () => reject(reader.error ?? new Error('file could not be read'));
    reader.readAsArrayBuffer(file);
  });
}

async function gzip(bytes: Bytes): Promise<Bytes> {
  const stream = new CompressionStream('gzip');
  // Set up the read first, only then write: a large input does not fit in the
  // stream's queue and blocks without a consumer.
  const packed = new Response(stream.readable).arrayBuffer();
  const writer = stream.writable.getWriter();
  await writer.write(bytes);
  await writer.close();
  return new Uint8Array(await packed);
}

/** Name of the archive; the extension decides how the ingest unpacks it. */
function archiveName(name: string): string {
  const bare = name
    .replace(/\.(tar\.gz|tgz|zip|html)$/i, '')
    .replace(/[^\p{Letter}\p{Number}._-]+/gu, '-')
    .replace(/^[-.]+|[-.]+$/g, '');
  return bare === '' ? 'site' : bare;
}

/** Builds the tar without compression; exposed so it can be tested. */
export async function makeTar(
  files: BundleFile[],
  onProgress?: (done: number, total: number) => void,
): Promise<Bytes> {
  const timestamp = Math.floor(Date.now() / 1000);
  const parts: Bytes[] = [];
  for (const [index, item] of files.entries()) {
    const bytes = await readBytes(item.file);
    parts.push(header(item.path, bytes.length, timestamp), bytes, padding(bytes.length));
    onProgress?.(index + 1, files.length);
  }
  // Two empty blocks terminate a tar.
  parts.push(new Uint8Array(2 * BLOCK));
  return concat(parts);
}

/** Packs the bundle into one `.tar.gz` the deploy API can handle. */
export async function pack(
  name: string,
  files: BundleFile[],
  onProgress?: (done: number, total: number) => void,
): Promise<File> {
  const tar = await makeTar(files, onProgress);
  return new File([await gzip(tar)], `${archiveName(name)}.tar.gz`, {
    type: 'application/gzip',
  });
}
