import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import Notices from './Notices.vue';

type Variant = 'neutral' | 'accent' | 'success' | 'warning' | 'critical';

/**
 * Without the design system loaded, nldd-notification is a plain unknown
 * element, so Vue sets its props as attributes; that is where this test reads
 * them.
 */
function makeWrapper() {
  const wrapper = mount(Notices);
  const notify = (wrapper.vm as unknown as {
    notify: (variant: Variant, text: string, detail?: string, duration?: number) => void;
  }).notify;
  return { wrapper, notify };
}

function durations(wrapper: ReturnType<typeof mount>): (string | undefined)[] {
  return wrapper.findAll('nldd-notification').map((el) => el.attributes('duration'));
}

describe('Notifications', () => {
  it("gives a confirmation 6 seconds, shorter than the design system's 10", async () => {
    const { wrapper, notify } = makeWrapper();

    notify('success', 'Zichtbaarheid opgeslagen', 'Openbaar');
    await wrapper.vm.$nextTick();

    const notice = wrapper.find('nldd-notification');
    expect(notice.attributes('duration')).toBe('6000');
    expect(notice.attributes('variant')).toBe('success');
    expect(notice.attributes('text')).toBe('Zichtbaarheid opgeslagen');
    expect(notice.attributes('supporting-text')).toBe('Openbaar');
  });

  it('leaves an error up until it is dismissed', async () => {
    const { wrapper, notify } = makeWrapper();

    notify('critical', 'Opslaan is niet gelukt', 'Probeer het opnieuw.');
    await wrapper.vm.$nextTick();

    // 0 = no timer, and nldd-notification ignores a duration on critical anyway.
    expect(wrapper.find('nldd-notification').attributes('duration')).toBe('0');
  });

  it("lets a given duration take precedence over the variant's default", async () => {
    const { wrapper, notify } = makeWrapper();

    notify('success', 'Versie live gezet', 'Een lange toelichting.', 9000);
    notify('critical', 'Versie niet live gezet', undefined, 4000);
    await wrapper.vm.$nextTick();

    expect(durations(wrapper)).toEqual(['9000', '4000']);
  });

  it('stacks notifications in order and removes only the dismissed one', async () => {
    const { wrapper, notify } = makeWrapper();

    notify('success', 'Eerste');
    notify('critical', 'Tweede');
    await wrapper.vm.$nextTick();
    expect(wrapper.findAll('nldd-notification')).toHaveLength(2);

    wrapper.findAll('nldd-notification')[0].element.dispatchEvent(new CustomEvent('dismiss'));
    await wrapper.vm.$nextTick();

    const rest = wrapper.findAll('nldd-notification');
    expect(rest).toHaveLength(1);
    expect(rest[0].attributes('text')).toBe('Tweede');
  });
});
