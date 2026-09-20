/**
 * Minimal ustar writer plus gzip: builds a .tar.gz in memory from a map of
 * files, without extra dependencies. Enough for the dist fixtures of the E2E
 * suite (regular files in subdirectories; no symlinks or specials).
 */

import { gzipSync } from 'node:zlib';

const BLOCK = 512;

function octal(value: number, length: number): Buffer {
  const text = value.toString(8).padStart(length - 1, '0');
  return Buffer.from(`${text}\0`, 'ascii');
}

function header(path: string, size: number): Buffer {
  const buffer = Buffer.alloc(BLOCK);
  buffer.write(path, 0, 100, 'utf8');
  octal(0o644, 8).copy(buffer, 100);
  octal(0, 8).copy(buffer, 108);
  octal(0, 8).copy(buffer, 116);
  octal(size, 12).copy(buffer, 124);
  octal(Math.floor(Date.now() / 1000), 12).copy(buffer, 136);
  buffer.fill(' ', 148, 156); // the checksum field counts as spaces
  buffer.write('0', 156); // typeflag: regular file
  buffer.write('ustar\0', 257, 'ascii');
  buffer.write('00', 263, 'ascii');

  let sum = 0;
  for (const byte of buffer) {
    sum += byte;
  }
  const checksum = Buffer.from(`${sum.toString(8).padStart(6, '0')}\0 `, 'ascii');
  checksum.copy(buffer, 148);
  return buffer;
}

export function makeTarGz(files: Record<string, string>): Buffer {
  const parts: Buffer[] = [];
  for (const [path, content] of Object.entries(files)) {
    const data = Buffer.from(content, 'utf8');
    parts.push(header(path, data.length));
    parts.push(data);
    const rest = data.length % BLOCK;
    if (rest !== 0) {
      parts.push(Buffer.alloc(BLOCK - rest));
    }
  }
  parts.push(Buffer.alloc(2 * BLOCK));
  return gzipSync(Buffer.concat(parts));
}
