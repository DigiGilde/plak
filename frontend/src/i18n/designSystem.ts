/**
 * Puts the interface language into the design system's own components.
 *
 * `@nldd/design-system` renders text of its own that we never write: the
 * picker on a combo box says "Toon opties", the footer navigations are called
 * "Kruimelpad" and "Juridische links", the close button of a notification is
 * "Sluit". In an English interface those stay Dutch, and an accessible label
 * is the worse half of that: the page declares `lang="en"`, so a screen reader
 * reads the Dutch out with English phonetics (WCAG 3.1.2).
 *
 * Each component takes a `translations` property that overrides its keys one
 * by one, and the package has no setter for the whole of it. Rather than hang
 * that object on all sixty-odd elements in the templates, where the next one
 * added would quietly be the one nobody remembers, this walks the document:
 * every `nldd-*` element we have words for gets them when it appears, and all
 * of them again when the language changes under `/-/profile`.
 *
 * Dutch stays the fallback, because it is the component's own default and this
 * interface's first language. `designSystem.nl.ts` repeats those defaults
 * literally so the Dutch interface reads exactly as it did.
 */
import { watch } from 'vue';

import { currentLocale, t } from './index';

/**
 * Which of our words answer which key, per element. Only components this app
 * uses, and only keys whose Dutch can reach someone;
 * `tests/design-system-text.test.ts` holds both halves of that claim against
 * the package.
 */
export function wordsPerElement(): Record<string, Record<string, string>> {
  return {
    'nldd-activity-indicator': {
      'components.activity-indicator.loading-label': t('designSystem.activityIndicator.loading'),
    },
    'nldd-banner': {
      'components.banner.dismiss-action': t('designSystem.banner.dismiss'),
    },
    'nldd-breadcrumbs': {
      'components.breadcrumbs.accessible-label': t('designSystem.breadcrumbs.label'),
    },
    'nldd-code-viewer': {
      'components.code-viewer.region-label': t('designSystem.codeViewer.region'),
      'components.code-viewer.copy-action': t('designSystem.codeViewer.copy'),
      'components.code-viewer.copy-success-text': t('designSystem.codeViewer.copied'),
      'components.code-viewer.copy-failure-text': t('designSystem.codeViewer.copyFailed'),
    },
    'nldd-combo-box': {
      'components.combo-box.open-menu-action': t('designSystem.comboBox.openMenu'),
      'components.combo-box.clear-action': t('designSystem.comboBox.clear'),
    },
    'nldd-file-field': {
      'components.file-field.to-choose-file-action': t('designSystem.fileField.choose'),
      'components.file-field.no-file-chosen-text': t('designSystem.fileField.none'),
      'components.file-field.clear-action': t('designSystem.fileField.clear'),
      'components.file-field.required-error-text': t('designSystem.fileField.required'),
    },
    'nldd-link': {
      'components.link.opens-in-new-tab-label': t('designSystem.link.newTab'),
    },
    'nldd-list': {
      'components.list.arrow-navigation-description-text': t('designSystem.list.arrowKeys'),
    },
    'nldd-notification': {
      'components.notification.dismiss-action': t('designSystem.notification.dismiss'),
      'components.notification.region-label': t('designSystem.notification.region'),
    },
    // The legal bar renders its own nav, so the label belongs to it and not to
    // the footer around it, although the key is named after the footer.
    'nldd-page-footer-legal-bar': {
      'components.page-footer.legal-bar-accessible-label': t('designSystem.pageFooter.legalLinks'),
    },
    'nldd-progress-bar': {
      'components.progress-bar.completed-suffix-text': t(
        'designSystem.progressBar.completedSuffix',
      ),
      'components.progress-bar.total-prefix-text': t('designSystem.progressBar.totalPrefix'),
      'components.progress-bar.loading-label': t('designSystem.progressBar.loading'),
      'components.progress-bar.accessible-label': t('designSystem.progressBar.label'),
    },
    'nldd-search-field': {
      'components.search-field.clear-action': t('designSystem.searchField.clear'),
      'components.search-field.search-action': t('designSystem.searchField.search'),
    },
    'nldd-split-button': {
      'components.split-button.menu-action': t('designSystem.splitButton.menu'),
    },
    'nldd-toolbar': {
      'components.toolbar.overflow-action': t('designSystem.toolbar.overflow'),
    },
  };
}

/** The elements worth looking for, as one selector. */
export const TRANSLATED_ELEMENTS = Object.keys(wordsPerElement());

const SELECTOR = TRANSLATED_ELEMENTS.join(',');

function dress(element: Element, words: Record<string, Record<string, string>>): void {
  const mine = words[element.localName];
  if (mine === undefined) return;
  (element as Element & { translations: Record<string, string> }).translations = mine;
}

function dressAll(root: ParentNode, words: Record<string, Record<string, string>>): void {
  for (const element of root.querySelectorAll(SELECTOR)) dress(element, words);
}

/**
 * Start handing the words out under `root`, and keep doing it. Returns the
 * way to stop, which only the tests need: in the app this runs for as long as
 * the page does.
 */
export function useDesignSystemText(root: ParentNode = document): () => void {
  let words = wordsPerElement();
  dressAll(root, words);

  const observer = new MutationObserver((records) => {
    for (const record of records) {
      for (const added of record.addedNodes) {
        if (!(added instanceof Element)) continue;
        dress(added, words);
        dressAll(added, words);
      }
    }
  });
  observer.observe(root, { childList: true, subtree: true });

  const stopWatching = watch(currentLocale, () => {
    words = wordsPerElement();
    dressAll(root, words);
  });

  return () => {
    observer.disconnect();
    stopWatching();
  };
}
