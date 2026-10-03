import '@nldd/design-system';

import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/api/client';
import * as plakApi from '@/api/plak';
import type { Volume } from '@/api/types';

import VolumeUsage from './VolumeUsage.vue';

vi.mock('@/api/plak', () => ({ platformStorage: vi.fn() }));

const MIB = 1024 * 1024;

function volume(overrides: Partial<Volume> = {}): Volume {
  return {
    totalBytes: 1024 * MIB,
    usedBytes: 500 * MIB,
    freeBytes: 524 * MIB,
    reserveBytes: 100 * MIB,
    maxDeployBytes: 200 * MIB,
    ...overrides,
  };
}

function property(el: Element, name: string): string {
  return (el as unknown as Record<string, string | undefined>)[name] ?? '';
}

async function mountWith(data: Volume) {
  vi.mocked(plakApi.platformStorage).mockResolvedValue(data);
  const wrapper = mount(VolumeUsage);
  await flushPromises();
  return wrapper;
}

function texts(wrapper: ReturnType<typeof mount>, selector: string): string[] {
  return wrapper.findAll(selector).map((cell) => property(cell.element, 'text'));
}

describe('VolumeUsage', () => {
  afterEach(() => {
    vi.mocked(plakApi.platformStorage).mockReset();
  });

  it('shows an indicator while the volume is being measured', async () => {
    vi.mocked(plakApi.platformStorage).mockReturnValue(new Promise(() => {}));
    const wrapper = mount(VolumeUsage);

    expect(wrapper.find('nldd-activity-indicator').exists()).toBe(true);
    expect(wrapper.find('nldd-list').exists()).toBe(false);
  });

  it('shows used, free and the reserve, and no site', async () => {
    const wrapper = await mountWith(volume());

    expect(texts(wrapper, 'nldd-text-cell')).toEqual([
      '500 MiB van 1 GiB',
      '524 MiB',
      '100 MiB',
    ]);
    expect(texts(wrapper, 'nldd-title-cell')).toEqual(['In gebruik', 'Vrij', 'Reserve']);
    expect(wrapper.find('nldd-banner').exists()).toBe(false);
    expect(property(wrapper.findAll('nldd-text-cell')[1]!.element, 'color')).toBe('content');
  });

  it('warns in red when a deploy of the maximum size would no longer fit', async () => {
    const wrapper = await mountWith(volume({ freeBytes: 240 * MIB, usedBytes: 784 * MIB }));

    const banner = wrapper.find('nldd-banner').element;
    expect(property(banner, 'variant')).toBe('critical');
    expect(property(banner, 'supportingText')).toContain('200 MiB');
    expect(property(banner, 'supportingText')).toContain('100 MiB');
    expect(property(wrapper.findAll('nldd-text-cell')[1]!.element, 'color')).toBe('critical');
  });

  it('does not warn at exactly the threshold', async () => {
    const wrapper = await mountWith(volume({ freeBytes: 300 * MIB }));

    expect(wrapper.find('nldd-banner').exists()).toBe(false);
  });

  it('never warns with the reserve switched off, and says so', async () => {
    const wrapper = await mountWith(volume({ reserveBytes: 0, freeBytes: 1 * MIB }));

    expect(wrapper.find('nldd-banner').exists()).toBe(false);
    expect(texts(wrapper, 'nldd-text-cell')[2]).toBe('Uit');
  });

  it('shows the refusal when the endpoint answers one', async () => {
    vi.mocked(plakApi.platformStorage).mockRejectedValue(
      new ApiError({ type: 'about:blank', title: 'Geen toegang', status: 403 }),
    );
    const wrapper = mount(VolumeUsage);
    await flushPromises();

    expect(property(wrapper.find('nldd-banner').element, 'text')).toBe('Geen toegang');
    expect(wrapper.find('nldd-list').exists()).toBe(false);
  });
});
