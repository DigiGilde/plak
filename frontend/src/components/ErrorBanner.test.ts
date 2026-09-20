import '@nldd/design-system';

import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import { ApiError } from '@/api/client';

import ErrorBanner from './ErrorBanner.vue';

/**
 * The design system is imported, so the custom elements upgrade and Vue sets
 * properties rather than attributes. Read the property.
 */
function prop(wrapper: ReturnType<typeof mount>, selector: string, name: string): unknown {
  const el = wrapper.find(selector).element as HTMLElement & Record<string, unknown>;
  return el[name];
}

function problem(status: number, title: string, detail?: string): ApiError {
  return new ApiError({ type: 'about:blank', title, status, detail });
}

describe('ErrorBanner', () => {
  it('shows the title and explanation of the error', () => {
    const wrapper = mount(ErrorBanner, {
      props: { error: problem(500, 'Interne fout', 'Er ging iets mis op de server.') },
    });

    expect(prop(wrapper, 'nldd-banner', 'variant')).toBe('critical');
    expect(prop(wrapper, 'nldd-banner', 'text')).toBe('Interne fout');
    expect(prop(wrapper, 'nldd-banner', 'supportingText')).toBe('Er ging iets mis op de server.');
    expect(wrapper.find('[data-testid="opnieuw-inloggen"]').exists()).toBe(false);
  });

  it('does not turn an expired session into a dead end', () => {
    // "Niet geauthenticeerd. Niet ingelogd." without a button left someone
    // with no way forward.
    const wrapper = mount(ErrorBanner, {
      props: { error: problem(401, 'Niet geauthenticeerd', 'Niet ingelogd.') },
    });

    expect(prop(wrapper, 'nldd-banner', 'text')).toBe('Je bent niet meer ingelogd');
    // Not an error but a state of affairs, so no critical color.
    expect(prop(wrapper, 'nldd-banner', 'variant')).toBe('warning');

    expect(prop(wrapper, '[data-testid="opnieuw-inloggen"]', 'text')).toBe('Opnieuw inloggen');
    expect(wrapper.find('[data-testid="opnieuw-inloggen"]').attributes('slot')).toBe('actions');
  });

  it('carries the current path along, so login brings you back where you were', () => {
    window.history.replaceState({}, '', '/nldd/website/access');

    const wrapper = mount(ErrorBanner, {
      props: { error: problem(401, 'Niet geauthenticeerd', 'Niet ingelogd.') },
    });

    expect(prop(wrapper, '[data-testid="opnieuw-inloggen"]', 'href')).toBe(
      '/-/login?returnTo=%2Fnldd%2Fwebsite%2Faccess',
    );
  });

  it('falls back to a comprehensible sentence for an error that is not an ApiError', () => {
    const wrapper = mount(ErrorBanner, { props: { error: new TypeError('netwerk weg') } });

    expect(prop(wrapper, 'nldd-banner', 'text')).toBe('Er ging iets mis');
    expect(prop(wrapper, 'nldd-banner', 'supportingText')).toContain('herlaad');
  });
});
