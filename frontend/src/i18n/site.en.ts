/**
 * English counterpart of site.nl.ts. Typed against it, so a key present in
 * one language and missing from the other is a compile error rather than a
 * raw key on screen.
 */
import type { siteNl } from './site.nl';

export const siteEn: Record<keyof typeof siteNl, string> = {
  // -- The page itself: loading, tab bar, unknown site ----------------------
  'site.loading': 'Loading site',
  'site.notFound.title': 'Unknown site',
  'site.notFound.detail': 'No site "{site}" in group "{group}".',
  'site.tabs.label': 'Site sections',
  'site.tabs.overview': 'Overview',
  'site.tabs.previews': 'Previews',
  'site.tabs.versions': 'Versions',
  'site.tabs.access': 'Access',
  'site.tabs.members': 'Members',
  'site.tabs.deploy': 'Deploy',

  // -- Overview tab ---------------------------------------------------------
  'site.overview.loading': 'Loading site details',
  'site.overview.status.heading': 'Status',
  'site.overview.live': 'Live',
  'site.overview.addressLabel.public': 'Public URL',
  'site.overview.addressLabel.restricted': 'Address of your site',
  'site.overview.copyAddress': 'Copy address',
  'site.overview.openSite': 'Open site',
  'site.overview.copied': 'Address copied.',
  'site.overview.copyFailed':
    'Copying did not work. Select the address above and copy it yourself.',
  'site.overview.lastDeploy': 'Last deploy: {timestamp}',
  'site.overview.noLiveVersion': 'No live version yet',
  'site.overview.noLiveVersion.hint':
    'Publish a first version with the form below or through the Deploy tab.',
  'site.overview.publish.heading': 'Publish a new version',
  'site.overview.published.title': 'Version published',
  'site.overview.published.detail': 'The new version is live now.',
  'site.overview.danger.heading': 'Danger zone',
  'site.overview.danger.body':
    'Deleting the site permanently removes every version, preview, invitee, secret link and the connected repository, including the files on the server.',
  'site.overview.danger.action': 'Delete site',
  'site.overview.danger.confirm.title': 'Delete site {group}/{site}?',
  'site.overview.danger.confirm.text':
    'This cannot be undone. Every version, preview, invitee, secret link and the connected repository disappear for good.',
  'site.overview.danger.confirm.keep': 'Keep site',
  'site.overview.danger.confirm.confirm': 'Delete this site',
  'site.overview.delete.failed': 'Site not deleted',
  'site.overview.delete.failed.detail': 'Deleting did not work.',

  // -- Previews tab ---------------------------------------------------------
  'site.previews.loading': 'Loading previews',
  'site.previews.heading': 'Previews',
  'site.previews.listLabel': 'Previews',
  'site.previews.intro':
    'Every preview lives on a ref of its own (for example pr-42) and expires 30 days after the last deploy. Access follows the site, unless you set access of its own below; that is then the whole access, base and exceptions together.',
  'site.previews.empty': 'No previews',
  'site.previews.empty.hint':
    'Publish a preview with a preview ref through the Deploy tab or the CI action.',
  'site.previews.updated': 'Updated {timestamp}',
  'site.previews.expires': 'Expires {timestamp}',
  'site.previews.accessChoice': 'Access',
  'site.previews.accessOverline': 'Access: {access}',
  'site.previews.sameAsSite': 'Same as site',
  'site.previews.keys.turnOn': 'Turn on secret links',
  'site.previews.keys.turnOff': 'Turn off secret links',
  'site.previews.invitees.turnOn': 'Turn on invitees',
  'site.previews.invitees.turnOff': 'Turn off invitees',
  'site.previews.remove': 'Delete preview',
  'site.previews.accessFailed': 'Access to {ref} not saved',
  'site.previews.accessFailed.detail': 'Saving did not work.',
  'site.previews.removeFailed': 'Preview {ref} not deleted',
  'site.previews.removeFailed.detail': 'Deleting did not work.',

  // -- Versions tab ---------------------------------------------------------
  'site.versions.loading': 'Loading versions',
  'site.versions.heading': 'Versions',
  'site.versions.listLabel': 'Live versions',
  'site.versions.intro':
    'The live history of this site. View an older version before you put it back; that view is only open to whoever has a role on this site or on its group.',
  'site.versions.storage.quota': 'This site uses {used} of {max}.',
  'site.versions.storage.noQuota': 'This site uses {used}.',
  'site.versions.retention.many':
    'The current and the {kept} previous versions are kept. Older ones are removed nightly.',
  'site.versions.retention.one':
    'The current and the previous version are kept. Older ones are removed nightly.',
  'site.versions.retention.all': 'All versions are kept.',
  'site.versions.keep.label': 'Previous versions kept',
  'site.versions.keep.default': 'Default: {count} previous versions',
  'site.versions.keep.default.one': 'Default: 1 previous version',
  'site.versions.keep.default.all': 'Default: all versions',
  'site.versions.keep.default.hint': 'Follows the platform setting.',
  'site.versions.keep.own': 'Custom number',
  'site.versions.keep.own.hint': 'A different number for this site.',
  'site.versions.keep.count.label': 'Number of previous versions',
  'site.versions.keep.count.help': '0 keeps every version.',
  'site.versions.keep.count.invalid': 'Enter a whole number of 0 or more.',
  'site.versions.keep.saved': 'Kept versions saved',
  'site.versions.keep.failed.problem': 'Not saved: {reason}. Try again.',
  'site.versions.keep.failed.network': 'Not saved. Check your connection and try again.',
  'site.versions.retention.own': "This is this site's own setting.",
  'site.versions.empty': 'No live versions yet',
  'site.versions.empty.hint':
    'Publish a first version through the Overview or the Deploy tab.',
  'site.versions.marker.live': 'Is live now',
  'site.versions.marker.notLive': 'Is not live',
  'site.versions.view': 'View',
  'site.versions.view.details': 'new tab',
  'site.versions.setLive': 'Make this version live',
  'site.versions.origin.uploadBy': 'Uploaded by {name}',
  'site.versions.origin.upload': 'Uploaded by hand',
  'site.versions.origin.repository': 'Published by {repository}',
  'site.versions.origin.ci': 'Published by CI',
  'site.versions.setLive.done': 'Version made live',
  'site.versions.setLive.done.detail': 'The version from {timestamp} is live now.',
  'site.versions.setLive.failed': 'Version not made live',
  'site.versions.setLive.failed.detail': 'Making it live did not work.',

  // -- Members tab ----------------------------------------------------------
  'site.members.loading': 'Loading members',
  'site.members.heading': 'Members',
  'site.members.intro':
    'Someone can reach this site via the group or via a role on this site alone. The wider of the two decides what they may do here: a site role widens, and never takes anything away.',

  // -- Secret link: the two ways to share one key ---------------------------
  'site.secretLink.withCode': 'Link with code',
  'site.secretLink.withCode.hint':
    'Anyone who has this link can view the page. Handy for a chat or an email to people who may all see it.',
  'site.secretLink.copyLink': 'Copy link',
  'site.secretLink.copyLink.label': 'Copy the link with code',
  'site.secretLink.linkCopied': 'Link copied.',
  'site.secretLink.copyLinkFailed':
    'Copying did not work. Select the link above and copy it yourself.',
  'site.secretLink.withoutCode': 'Link without code',
  'site.secretLink.withoutCode.hint':
    'Send the link through one channel and the code through another. The reader is asked for the code once.',
  'site.secretLink.code': 'Code',
  'site.secretLink.copyLinkWithoutCode': 'Copy link without code',
  'site.secretLink.linkWithoutCodeCopied': 'Link without code copied.',
  'site.secretLink.copyCode': 'Copy code',
  'site.secretLink.codeCopied': 'Code copied.',
  'site.secretLink.copyCodeFailed':
    'Copying did not work. Select the code above and copy it yourself.',
};
