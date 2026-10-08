/**
 * English counterpart of address.nl.ts. Typed against it, so a key present in
 * one language and missing from the other is a compile error rather than a
 * raw key on screen.
 */
import type { addressNl } from './address.nl';

export const addressEn: Record<keyof typeof addressNl, string> = {
  // -- Headings ---------------------------------------------------------------
  'address.group.heading': 'Address of the group',
  'address.site.heading': 'Address of the site',

  // -- Where it is, and where it used to be -----------------------------------
  'address.current.site': 'The address of this site is {address}.',
  'address.current.group':
    'The address of this group is {slug}. It is part of the address of every site, for example {example}.',
  'address.current.group.empty':
    'The address of this group is {slug}. It is part of the address of every site in this group.',
  'address.previous': '{address} redirects up to and including {date}.',
  'address.restore': 'Change back',
  'address.restore.label': 'Change back to {address}',

  // -- Who may change it ------------------------------------------------------
  'address.readOnly.group': 'Only an administrator of the group can change the address.',
  'address.readOnly.site':
    'Only an administrator of the site who is also a member of the group can change the address.',
  'address.readOnly.siteOnly':
    'You administer this site, but you are not a member of the group. Only a site administrator who is also a member of the group can change the address.',

  // -- The warning for an address that has been shared ------------------------
  'address.public.site': 'This site is public.',
  'address.public.site.detail':
    'People may have saved the address, put it in a document or printed it. Only change the address if you really have to.',
  'address.public.group': 'This group has public sites.',
  'address.public.group.detail':
    'Their addresses may be in documents, e-mails or on paper. Only change the address if you really have to.',

  // -- The field ----------------------------------------------------------------
  'address.field.label': 'New address',
  'address.field.required': 'An address is needed',
  'address.field.rule':
    'An address contains only lowercase letters, digits and hyphens, does not start or end with a hyphen and is at most 63 characters long',
  'address.field.differs': 'The new address differs from the current address',
  'address.field.preview': 'The new address will be {address}',
  'address.field.preview.empty': 'The new address appears here as soon as you enter it.',
  'address.placeholder.new': '<new address>',
  'address.placeholder.site': '<site>',

  // -- What it comes to, said before the button -------------------------------
  'address.consequences.self': 'You need to change this yourself:',
  'address.consequences.workflow.site':
    'Automatic publishing stops right away. Change {site} in your workflow and {flag} in {command} to {address}, and add {siteId} if it is not there yet.',
  'address.consequences.workflow.group':
    'Automatic publishing stops right away, for every site in the group. Change {site} in your workflow and {flag} in {command} to {address}, and add the site ID of each site if it is not there yet.',
  'address.consequences.content':
    'Is the old address in your site, for example in {canonical}, {ogUrl}, a sitemap or a fixed path? Then publish the site again with the new address before {date}. Up to and including {date}, styles and scripts still load through the old address; after that they do not.',
  'address.consequences.secretLink':
    'Did you share a secret link? After {date}, replace the address in the link with the new address. Everything after {key} stays the same.',
  'address.consequences.changes': 'This is what changes:',
  'address.consequences.sites.many':
    'All {count} sites in this group get a new address. For example: {from} becomes {to}.',
  'address.consequences.sites.one': 'The site in this group gets a new address.',
  'address.consequences.redirect':
    'The old address redirects visitors to the new address, up to and including {date}. That only applies to people who may view the site.',
  'address.consequences.expiry.site':
    'After that date the old address stops working. Another site can then get this address, and an old link leads to other content.',
  'address.consequences.expiry.group':
    'After that date the old address stops working. Another group can then get this address, and an old link leads to other content.',
  'address.consequences.login': 'Visitors to a restricted site may have to sign in again.',
  'address.consequences.previews':
    'Previews get a new address too. Links to previews, for example in a pull request, keep working up to and including {date}.',
  'address.consequences.undo': 'Up to and including {date} you can change the old address back.',

  // -- The button and the confirmation ----------------------------------------
  'address.change': 'Change address',
  'address.confirm.title.site': 'Change the address of site {from} to {to}?',
  'address.confirm.title.group': 'Change the address of group {from} to {to}?',
  'address.confirm.text':
    'Old links redirect up to and including {date}. Publishing from a workflow only works again once you set the new address.',
  'address.confirm.text.group.many':
    'All {count} sites get a new address. Old links redirect up to and including {date}. Publishing from a workflow only works again once you set the new address.',
  'address.confirm.text.group.one':
    'The site gets a new address. Old links redirect up to and including {date}. Publishing from a workflow only works again once you set the new address.',
  'address.confirm.keep': 'Keep current address',
  'address.confirm.confirm': 'Change address',

  // -- What came of it --------------------------------------------------------
  'address.changed':
    'The address has been changed. {from} redirects to {to} up to and including {date}. Do you publish automatically or with {command}? Change the address there now.',
  'address.error.invalid': 'This address cannot be used. Choose another address.',
  'address.error.exists.site':
    'This address is already in use, or recently belonged to another site. Choose another address.',
  'address.error.exists.group':
    'This address is already in use, or recently belonged to another group. Choose another address.',
  'address.error.tooMany.site':
    'This site already has five old addresses that still redirect. Change one back, or wait until one has been released.',
  'address.error.tooMany.group':
    'This group already has five old addresses that still redirect. Change one back, or wait until one has been released.',
  'address.error.failed': 'Address not changed',
  'address.error.failed.detail': 'Changing it did not work.',
};
