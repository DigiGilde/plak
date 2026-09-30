/**
 * The words the design system says by itself.
 *
 * `@nldd/design-system` carries its own Dutch for the text it renders without
 * being told: the picker on a combo box, the label of a footer navigation, the
 * button that closes a notification. There is no way to set a language for the
 * package as a whole, only a `translations` property per component, so this
 * section holds those strings on our side and `designSystem.ts` hands them to
 * the elements.
 *
 * Every Dutch value here is the package's own default, character for character.
 * That is what keeps the Dutch interface exactly as it was, and
 * `tests/design-system-text.test.ts` checks it against the package on every
 * run: a word the design system rewrites is a word we have to look at again
 * rather than silently keep saying.
 */
export const designSystemNl = {
  'designSystem.activityIndicator.loading': 'Laden',
  'designSystem.banner.dismiss': 'Verberg',
  'designSystem.breadcrumbs.label': 'Kruimelpad',
  'designSystem.codeViewer.region': 'Code',
  'designSystem.codeViewer.copy': 'Kopieer',
  'designSystem.codeViewer.copied': 'Gekopieerd',
  'designSystem.codeViewer.copyFailed': 'Kopiëren mislukt',
  'designSystem.comboBox.openMenu': 'Toon opties',
  'designSystem.comboBox.clear': 'Wis invoer',
  'designSystem.fileField.choose': 'Bestand kiezen',
  'designSystem.fileField.none': 'Geen bestand gekozen',
  'designSystem.fileField.clear': 'Wis selectie',
  'designSystem.fileField.required': 'Kies een bestand',
  'designSystem.link.newTab': 'Opent in nieuw tabblad',
  'designSystem.list.arrowKeys': 'Gebruik de pijltjestoetsen om door de lijst te navigeren.',
  'designSystem.notification.dismiss': 'Sluit',
  'designSystem.notification.region': 'Meldingen',
  'designSystem.pageFooter.legalLinks': 'Juridische links',
  'designSystem.progressBar.completedSuffix': 'voltooid',
  'designSystem.progressBar.totalPrefix': 'Totaal',
  'designSystem.progressBar.loading': 'Aan het laden',
  'designSystem.progressBar.label': 'Voortgang',
  'designSystem.searchField.search': 'Zoek',
  'designSystem.searchField.clear': 'Wis zoekopdracht',
  'designSystem.splitButton.menu': 'Meer opties',
  'designSystem.toolbar.overflow': 'Meer',
} as const;
