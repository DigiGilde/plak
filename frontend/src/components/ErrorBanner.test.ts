import '@nldd/design-system';

import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

import { ApiError } from '@/api/client';
import { _setLocaleForTest } from '@/i18n';

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
    expect(wrapper.find('[data-testid="sign-in-again"]').exists()).toBe(false);
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

    expect(prop(wrapper, '[data-testid="sign-in-again"]', 'text')).toBe('Opnieuw inloggen');
    expect(wrapper.find('[data-testid="sign-in-again"]').attributes('slot')).toBe('actions');
  });

  it('carries the current path along, so login brings you back where you were', () => {
    window.history.replaceState({}, '', '/team-aurora/website/access');

    const wrapper = mount(ErrorBanner, {
      props: { error: problem(401, 'Niet geauthenticeerd', 'Niet ingelogd.') },
    });

    expect(prop(wrapper, '[data-testid="sign-in-again"]', 'href')).toBe(
      '/-/login?returnTo=%2Fteam-aurora%2Fwebsite%2Faccess',
    );
  });

  describe('when a site or group may have been renamed', () => {
    it('points to the overview on a page that asks for it, when nothing is found there', () => {
      const wrapper = mount(ErrorBanner, {
        props: { error: problem(404, 'Onbekende groep', 'Geen groep "x".'), renamedHint: true },
      });

      expect(wrapper.find('[data-testid="renamed-hint"]').text()).toBe(
        'Is de site of groep hernoemd? Zoek hem dan in je overzicht.',
      );
      const link = wrapper.find('[data-testid="renamed-overview"]');
      expect(prop(wrapper, '[data-testid="renamed-overview"]', 'text')).toBe('Naar het overzicht');
      expect(prop(wrapper, '[data-testid="renamed-overview"]', 'href')).toBe('/');
      expect(link.attributes('slot')).toBe('actions');
      // The refusal itself stays as it was told.
      expect(prop(wrapper, 'nldd-banner', 'text')).toBe('Onbekende groep');
      expect(prop(wrapper, 'nldd-banner', 'supportingText')).toBe('Geen groep "x".');
    });

    it('keeps the hint to a not found: another failure is not a rename', () => {
      const wrapper = mount(ErrorBanner, {
        props: { error: problem(500, 'Interne fout', 'Mis.'), renamedHint: true },
      });

      expect(wrapper.find('[data-testid="renamed-hint"]').exists()).toBe(false);
      expect(wrapper.find('[data-testid="renamed-overview"]').exists()).toBe(false);
    });

    it('keeps the hint to an error of the API: another failure is not a rename', () => {
      const wrapper = mount(ErrorBanner, {
        props: { error: new TypeError('netwerk weg'), renamedHint: true },
      });

      expect(wrapper.find('[data-testid="renamed-hint"]').exists()).toBe(false);
    });

    it('says nothing of it unless the page asks, which is the page of a site or a group', () => {
      const wrapper = mount(ErrorBanner, {
        props: { error: problem(404, 'Onbekend', 'Niet gevonden.') },
      });

      expect(wrapper.find('[data-testid="renamed-hint"]').exists()).toBe(false);
      expect(wrapper.find('[data-testid="renamed-overview"]').exists()).toBe(false);
    });

    it('says it in English in English', () => {
      _setLocaleForTest('en');
      const wrapper = mount(ErrorBanner, {
        props: { error: problem(404, 'Unknown site', 'No site "x".'), renamedHint: true },
      });

      expect(wrapper.find('[data-testid="renamed-hint"]').text()).toBe(
        'Was the site or group renamed? Then look for it in your overview.',
      );
      expect(prop(wrapper, '[data-testid="renamed-overview"]', 'text')).toBe('Go to the overview');
    });
  });

  it('falls back to a comprehensible sentence for an error that is not an ApiError', () => {
    const wrapper = mount(ErrorBanner, { props: { error: new TypeError('netwerk weg') } });

    expect(prop(wrapper, 'nldd-banner', 'text')).toBe('Er ging iets mis');
    expect(prop(wrapper, 'nldd-banner', 'supportingText')).toContain('herlaad');
  });
});
