import { mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';

import { expectNoAxeViolations } from '../../../tests/a11y';

import SecretLink from './SecretLink.vue';

const SITE = 'https://sites.plak.test/team-aurora/website/';
const VALUE = 'AbCdEfGh.geheimeverifier';

function makeWrapper() {
  return mount(SecretLink, {
    props: { value: VALUE, siteUrl: SITE, prefix: 'new-key' },
  });
}

function withClipboard(write: ReturnType<typeof vi.fn>): void {
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true });
}

describe('Secret link: two ways to share', () => {
  it('shows the full link, the link without the code and the code separately', () => {
    const wrapper = makeWrapper();

    expect(wrapper.find('[data-testid="new-key-link"]').text()).toBe(
      `${SITE}?key=${VALUE}`,
    );
    expect(wrapper.find('[data-testid="new-key-link-without-code"]').text()).toBe(
      `${SITE}?key=AbCdEfGh`,
    );
    expect(wrapper.find('[data-testid="new-key-code"]').text()).toBe('geheimeverifier');
  });

  it('does not carry the code in the link without the code', () => {
    const wrapper = makeWrapper();

    const bare = wrapper.find('[data-testid="new-key-link-without-code"]');
    expect(bare.text()).not.toContain('geheimeverifier');
    expect(bare.attributes('href')).toBe(`${SITE}?key=AbCdEfGh`);
  });

  it.each([
    ['new-key-copy', `${SITE}?key=${VALUE}`, 'Link gekopieerd.'],
    ['new-key-copy-without-code', `${SITE}?key=AbCdEfGh`, 'Link zonder code gekopieerd.'],
    ['new-key-code-copy', 'geheimeverifier', 'Code gekopieerd.'],
  ])('copies via %s and confirms it beside the button', async (testid, copied, notice) => {
    const write = vi.fn().mockResolvedValue(undefined);
    withClipboard(write);
    const wrapper = makeWrapper();

    await wrapper.find(`[data-testid="${testid}"]`).trigger('click');
    await wrapper.vm.$nextTick();

    expect(write).toHaveBeenCalledWith(copied);
    const line = wrapper.find('[data-testid="new-key-notice"]');
    expect(line.text()).toBe(notice);
    expect(line.attributes('role')).toBe('status');
  });

  it('points the way to the value itself when the clipboard is refused', async () => {
    withClipboard(vi.fn().mockRejectedValue(new Error('geen toestemming')));
    const wrapper = makeWrapper();

    await wrapper.find('[data-testid="new-key-copy"]').trigger('click');
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="new-key-notice"]').text()).toContain(
      'Selecteer de link',
    );

    await wrapper.find('[data-testid="new-key-code-copy"]').trigger('click');
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[data-testid="new-key-notice"]').text()).toContain(
      'Selecteer de code',
    );
  });

  it('says in one line what each of the two ways is for', () => {
    const html = makeWrapper().html();

    expect(html).toContain('Link met code');
    expect(html).toContain('Link zonder code');
    expect(html).toContain('Handig voor een chat of mail');
    expect(html).toContain('het ene kanaal en de code via het andere');
  });

  it('has no axe violations', async () => {
    // Attached to the document: axe runs against a page, not a loose fragment.
    const wrapper = mount(SecretLink, {
      props: { value: VALUE, siteUrl: SITE, prefix: 'new-key' },
      attachTo: document.body,
    });

    await expectNoAxeViolations(wrapper.element);

    wrapper.unmount();
  });

  it('falls back to an empty code when the value has no dot', () => {
    const wrapper = mount(SecretLink, {
      props: { value: 'AbCdEfGh', siteUrl: SITE, prefix: 'done-key' },
    });

    expect(wrapper.find('[data-testid="done-key-code"]').text()).toBe('');
    expect(wrapper.find('[data-testid="done-key-link"]').text()).toBe(`${SITE}?key=AbCdEfGh`);
  });
});
