import { expect, it } from 'vitest';

import { appVersion } from './version';

it('is dev when the build was given no TOON_VERSION', () => {
  expect(appVersion()).toBe(process.env.TOON_VERSION || 'dev');
});
