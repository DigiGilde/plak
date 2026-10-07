import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import { defineComponent, h } from 'vue';

import { ApiError } from '@/api/client';
import { siteNotFound, useSiteGroup } from './siteGroup';

describe('useSiteGroup', () => {
  it('refuses to run outside a site page', () => {
    const Probe = defineComponent({
      setup() {
        useSiteGroup();
        return () => h('div');
      },
    });

    expect(() => mount(Probe)).toThrow('useSiteGroup needs a site page around it');
  });
});

describe('siteNotFound', () => {
  it('is a 404 that names the site and the group', () => {
    const failure = siteNotFound('team-aurora', 'weg');

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.problem.status).toBe(404);
    expect(failure.problem.detail).toContain('weg');
    expect(failure.problem.detail).toContain('team-aurora');
  });
});
