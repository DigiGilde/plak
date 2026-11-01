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
    'nldd-button': {
      'components.button.link-hint-new-tab-lowercase-label': t('designSystem.linkHint.newTab'),
      'components.button.link-hint-external-lowercase-label': t('designSystem.linkHint.external'),
      'components.button.link-hint-external-new-tab-lowercase-label': t(
        'designSystem.linkHint.externalNewTab',
      ),
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
      'components.file-field.to-change-action': t('designSystem.fileField.change'),
      'components.file-field.file-chosen-text': t('designSystem.fileField.chosen'),
      'components.file-field.clear-action': t('designSystem.fileField.clear'),
      'components.file-field.required-error-text': t('designSystem.fileField.required'),
      'components.file-field.drop-hint-lowercase-text': t('designSystem.fileField.dropHint'),
      'components.file-field.drag-here-text': t('designSystem.fileField.dragHere'),
      'components.file-field.drop-here-text': t('designSystem.fileField.dropHere'),
      'components.file-field.replace-drag-text': t('designSystem.fileField.replaceDrag'),
      'components.file-field.replace-drop-text': t('designSystem.fileField.replaceDrop'),
      'components.file-field.file-type-error-text': t('designSystem.fileField.typeError'),
      'components.file-field.wrong-file-type-error-text': t('designSystem.fileField.wrongType'),
      'components.file-field.list-lowercase-text': t('designSystem.fileField.typeList'),
      'components.file-field.too-many-files-error-text': t('designSystem.fileField.tooMany'),
      'components.file-field.folder-error-text': t('designSystem.fileField.folder'),
    },
    'nldd-link': {
      'components.link.link-hint-new-tab-lowercase-label': t('designSystem.linkHint.newTab'),
      'components.link.link-hint-external-lowercase-label': t('designSystem.linkHint.external'),
      'components.link.link-hint-external-new-tab-lowercase-label': t(
        'designSystem.linkHint.externalNewTab',
      ),
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
      'components.progress-bar.completed-suffix-lowercase-text': t(
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
    'nldd-tab-bar': {
      'components.tab-bar.overflow-action': t('designSystem.tabBar.overflow'),
    },
    'nldd-toolbar': {
      'components.toolbar.overflow-action': t('designSystem.toolbar.overflow'),
    },
  };
}

/** Marks a file field that also takes a whole folder, dragged or chosen. */
export const TAKES_FOLDERS = 'data-takes-folders';

/**
 * On a field marked {@link TAKES_FOLDERS}, the words that replace the package's
 * while something is dragged: those speak of one file, and a folder is just as
 * welcome there. Not the package's Dutch, so they live with the upload texts.
 */
export function folderFieldWords(): Record<string, string> {
  return {
    'components.file-field.drag-here-text': t('publish.upload.field.dragHere'),
    'components.file-field.drop-here-text': t('publish.upload.field.dropHere'),
  };
}

interface Words {
  perElement: Record<string, Record<string, string>>;
  folderField: Record<string, string>;
}

function allWords(): Words {
  return { perElement: wordsPerElement(), folderField: folderFieldWords() };
}

/** The elements worth looking for, as one selector. */
const TRANSLATED_ELEMENTS = Object.keys(wordsPerElement());

const SELECTOR = TRANSLATED_ELEMENTS.join(',');

function dress(element: Element, words: Words): void {
  let mine = words.perElement[element.localName];
  if (mine === undefined) return;
  if (element.localName === 'nldd-file-field' && element.hasAttribute(TAKES_FOLDERS)) {
    mine = { ...mine, ...words.folderField };
  }
  (element as Element & { translations: Record<string, string> }).translations = mine;
}

function dressAll(root: ParentNode, words: Words): void {
  for (const element of root.querySelectorAll(SELECTOR)) dress(element, words);
}

/**
 * Start handing the words out under `root`, and keep doing it. Returns the
 * way to stop, which only the tests need: in the app this runs for as long as
 * the page does.
 */
export function useDesignSystemText(root: ParentNode = document): () => void {
  let words = allWords();
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
    words = allWords();
    dressAll(root, words);
  });

  return () => {
    observer.disconnect();
    stopWatching();
  };
}
