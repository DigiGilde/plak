import { expect, it } from 'vitest';

import { appVersion } from './version';

it('is dev when the build was given no PLAK_VERSION', () => {
  expect(appVersion()).toBe(process.env.PLAK_VERSION || 'dev');
});
