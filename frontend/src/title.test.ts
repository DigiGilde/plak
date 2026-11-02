import { afterEach, describe, expect, it } from 'vitest';

import { setDocumentTitle } from './title';

afterEach(() => {
  document.title = '';
});

describe('Page title', () => {
  it('puts the distinguishing part first and the product name last', () => {
    setDocumentTitle('Jaarverslag 2025', 'Versies');

    expect(document.title).toBe('Jaarverslag 2025 - Versies - Toon');
  });

  it('does not name the product name twice', () => {
    setDocumentTitle('Over Toon');
    expect(document.title).toBe('Over Toon');

    // A site that happens to start with the same letters keeps its suffix.
    setDocumentTitle('Toonbank');
    expect(document.title).toBe('Toonbank - Toon');
  });

  it('leaves out parts that do not exist yet', () => {
    // While loading, a site's name is still unknown; the title may not
    // reserve a dash for it.
    setDocumentTitle(undefined, null, '');

    expect(document.title).toBe('Toon');
  });
});
