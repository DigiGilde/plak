/**
 * English counterpart of publish.nl.ts. Typed against it, so a key present in
 * one language and missing from the other is a compile error rather than a
 * raw key on screen.
 */
import type { publishNl } from './publish.nl';

export const publishEn: Record<keyof typeof publishNl, string> = {
  // -- Publish sheet: the flow from file to published site --------------------
  'publish.sheet.heading': 'Put a site online',
  'publish.sheet.close': 'Close',
  'publish.sheet.drop': 'Let go to upload',
  'publish.sheet.halfway.title': 'The site already exists',
  'publish.sheet.halfway.detail':
    "Only the file still has to go on it. Choose 'Put online' again.",
  'publish.sheet.submit': 'Put online',

  // -- Publish sheet: the fields ----------------------------------------------
  'publish.sheet.file.label': 'File',
  'publish.sheet.file.hint': 'A .zip or .tar.gz of your folder, or a single HTML file.',
  'publish.sheet.file.clear': 'Remove the chosen file',
  'publish.sheet.file.required': 'An archive (.zip, .tar.gz) or a single HTML file',
  'publish.sheet.title.label': 'Title',
  'publish.sheet.title.required': 'A title',
  'publish.sheet.group.label': 'Group',
  'publish.sheet.groupName.label': 'Name of your group',
  'publish.sheet.groupName.hintNone':
    'You do not have one yet; you create it now and are a member of it right away.',
  'publish.sheet.groupName.hintNoRole':
    'You are neither editor nor admin in any of your groups; create a new group to publish here.',
  'publish.sheet.groupName.required': 'A name for your group',
  'publish.sheet.address.label': 'Address',
  'publish.sheet.address.hint': 'The last part of the URL.',
  'publish.sheet.address.required': 'An address',
  'publish.sheet.address.form':
    'Lowercase letters, digits and hyphens only, no hyphen at the start or the end',
  'publish.sheet.address.known': 'Your site will be at {address}',
  'publish.sheet.address.empty': 'The address of your site follows from the title.',

  // -- Publish sheet: who may look --------------------------------------------
  'publish.sheet.visibility.label': 'Who can view the site?',
  'publish.sheet.extras.heading': 'Exceptions',
  'publish.sheet.extras.hint': 'These let people in, and never take anyone away.',
  'publish.sheet.extras.keysAfter': 'You get the first secret link right after publishing.',
  'publish.sheet.extras.inviteesAfter':
    'You add invitees to the list afterwards, on the Access tab of the site.',

  // -- Upload zone ------------------------------------------------------------
  'publish.drop.oneFile': 'Drag one file: a .zip, .tar.gz, .tgz or a single HTML file.',
  'publish.drop.noFolder': 'Do not drag a folder. {hint}',
  'publish.upload.field.label': 'File or folder',
  'publish.upload.field.hint': 'Drag it here, or choose it.',
  'publish.upload.field.required': 'An archive, a single HTML file or a folder you drag here',
  'publish.upload.field.help':
    'It takes an archive (.zip, .tar.gz or .tgz), a single HTML file, or a whole folder you drag here. A single HTML file becomes the start page of your site; a folder is packed by Plak in your browser, without the metadata of your operating system.',
  'publish.upload.progress.reading': 'Reading folder',
  'publish.upload.progress.packing': 'Packing folder',
  'publish.upload.ready.title': 'Ready to publish',
  'publish.upload.ready.file': '"{name}" is ready, {size}.',
  'publish.upload.ready.bundleOne': 'Folder "{name}" packed: {count} file, {size}.',
  'publish.upload.ready.bundle': 'Folder "{name}" packed: {count} files, {size}.',
  'publish.upload.error.title': 'This cannot be published as it is',
  'publish.upload.error.cannotPack':
    'This browser cannot pack a folder by itself. Make a .zip of it and choose that with "Choose file".',
  'publish.upload.error.packFailed':
    'Packing did not work. Make a .zip of your folder yourself and choose that with "Choose file".',
  'publish.upload.submit': 'Publish version',

  // -- Packing: what is refused before a byte goes over the wire ---------------
  'publish.packing.secret':
    '"{path}" does not belong on a website, so Plak will not publish this folder. Choose the folder with the built site, usually "dist" or "build", instead of the whole project folder.',
  'publish.packing.nothing': 'Nothing was dropped that can be published.',
  'publish.packing.notASite':
    'Plak cannot make a site out of "{name}" alone. Drag the whole folder, an archive (.zip, .tar.gz or .tgz), or a single HTML file.',
  'publish.packing.folderEmpty': 'There is nothing in this folder that can be published.',
  'publish.packing.bundleEmpty': 'There is nothing in "{name}" that can be published.',
  'publish.packing.tooManyFiles':
    '"{name}" has more than {count} files; that is more than one publication may hold.',
  'publish.packing.tooDeep':
    '"{path}" sits deeper than {depth} folders; make the folder structure shallower.',
  'publish.packing.pathTooLong':
    'The path "{path}" is too long for an archive; shorten the folder or file names.',
  'publish.packing.fileTooLarge':
    '"{path}" is {size}; a single file may be {max} at most.',
  'publish.packing.totalTooLarge':
    '"{name}" is {size} together; a publication may be {max} at most.',
  'publish.packing.indexCase':
    'The start page is called "{found}"; it has to be called "{index}", in lowercase.',
  'publish.packing.indexInFolder':
    'There is no {index} in the root of "{name}", but there is one at "{path}". Drag the folder "{folder}" itself.',
  'publish.packing.indexMissing':
    'There is no {index} in "{name}". Without a start page nobody can open the site.',

  // -- Deploy tab: the linked repository ---------------------------------------
  'publish.deploy.loading': 'Loading deploy settings',
  'publish.deploy.heading': 'Publishing from GitHub or Forgejo',
  'publish.deploy.intro':
    'Link the repository your site lives in. A push to the live branch then publishes a new version automatically.',
  'publish.deploy.repo.liveBranch': 'Live branch: {branch}',
  'publish.deploy.repo.anyBranch': 'any branch',
  'publish.deploy.repo.linkedBy': 'Linked by {who}',
  'publish.deploy.repo.unknownWho': 'unknown',
  'publish.deploy.repo.change': 'Change',
  'publish.deploy.repo.unlink': 'Unlink',
  'publish.deploy.repo.empty': 'No repository linked yet',
  'publish.deploy.repo.emptyAdmin':
    'Link a repository; a push to the live branch then publishes automatically.',
  'publish.deploy.repo.emptyReader':
    'Ask an admin of this site to link a repository; after that a push to the live branch publishes automatically.',
  'publish.deploy.repo.link': 'Link repository',
  'publish.deploy.repo.linked': 'Repository linked',
  'publish.deploy.repo.linkedDetail': '{repo} may now publish to this site.',
  'publish.deploy.repo.linkFailed': 'Linking did not work.',
  'publish.deploy.repo.unlinkFailed': 'Repository not unlinked',
  'publish.deploy.repo.unlinkFailedDetail': 'Unlinking did not work.',

  // -- Deploy tab: the link form ------------------------------------------------
  'publish.deploy.form.provider': 'Provider',
  'publish.deploy.form.host': 'Forgejo host',
  'publish.deploy.form.repo': 'Repository',
  'publish.deploy.form.repoPlaceholder': 'https://github.com/minbzk/website or minbzk/website',
  'publish.deploy.form.repoHelp':
    'Paste a URL from GitHub or Forgejo ("https://github.com/minbzk/website", "git@code.overheid.nl:minbzk/website.git"), or type "owner/repo".',
  'publish.deploy.form.repoRequired':
    'A repository URL or "owner/repo", for example "minbzk/website"',
  'publish.deploy.form.branch': 'Live branch',
  'publish.deploy.form.branchPlaceholder': 'e.g. main',
  'publish.deploy.form.branchHelp':
    'Empty: every branch of this repository may publish live, as long as the run is a push, a manual run (workflow_dispatch) or a schedule, never a pull request. Fill in a branch (for example "main") to let only that branch publish live; previews then stay possible from every branch.',
  'publish.deploy.form.cancel': 'Cancel',
  'publish.deploy.form.submit': 'Link',
  'publish.deploy.form.recognized': 'Recognised: {label}, {repo}',
  'publish.deploy.form.forgejoLabel': 'Forgejo ({host})',
  'publish.deploy.form.urlNoOwner': 'The URL holds no owner and repository.',
  'publish.deploy.form.unknownHost': 'Unknown host "{host}". Allowed: {allowed}.',
  'publish.deploy.form.invalidUrl': 'This is not a valid URL.',
  'publish.deploy.form.invalidReference':
    'Owner and repository separated by a slash (for example "minbzk/website"), or a repository URL.',

  // -- Deploy tab: the ready-made workflow ---------------------------------------
  'publish.deploy.workflow.hint':
    'A ready-made workflow for {provider} Actions. Replace {code} with the pinned commit of the action.',
  'publish.deploy.workflow.path':
    'Save this file at {path} in the repository. After a push to the live branch, the new version shows up in {link}.',

  // -- Deploy tab: why this is safe ------------------------------------------------
  'publish.deploy.safety.summary': 'Why is this safe?',
  'publish.deploy.safety.noSecret':
    'No secret is stored in the repository: the forge (GitHub or Forgejo) itself signs a short-lived token for exactly this workflow run, and Plak checks that signature.',
  'publish.deploy.safety.scoped':
    'That token only proves which repository the run started from; Plak accepts it here because you linked exactly that repository to this site.',
  'publish.deploy.safety.liveRestricted':
    'Publishing live only works from the live branch you set, and only from a push, a manual run or a schedule, never from a pull request.',
  'publish.deploy.safety.previews':
    'Previews are allowed from any branch, including a pull request, and never touch the live site.',
  'publish.deploy.safety.audited': 'Every publish is recorded in the audit log.',
  'publish.deploy.safety.unlink':
    'Unlink the repository and publishing from CI stops immediately.',

  // -- Deploy tab: publishing from your own computer -------------------------------
  'publish.deploy.cli.heading': 'From your own computer',
  'publish.deploy.cli.install':
    'With the plak cli you publish without CI, with your own access. If you do not have it yet: {repo} holds it in {folder}. Install it with {install}, update it with {upgrade}. If you work from a checkout, {run} runs it without installing.',
  'publish.deploy.cli.login':
    '{login} opens your browser at {link} to link the device; after that the cli keeps that session for later {publish} calls.',

  // -- Deploy tab: unlinking ---------------------------------------------------------
  'publish.deploy.unlink.title': 'Unlink repository?',
  'publish.deploy.unlink.text':
    'This site can no longer publish from CI without a secret until a new repository is linked.',
  'publish.deploy.unlink.keep': 'Keep the link',
  'publish.deploy.unlink.confirm': 'Unlink repository',

  // -- Access tab: the base ------------------------------------------------------
  'publish.access.loading': 'Loading access settings',
  'publish.access.unknownSite.title': 'Unknown site',
  'publish.access.unknownSite.detail': 'No site "{site}" in group "{group}".',
  'publish.access.base.heading': 'Who can view this site?',
  'publish.access.saved': 'Access saved',
  'publish.access.saveFailed': 'Access not saved',
  'publish.access.saveFailedDetail': 'Saving did not work.',
  'publish.access.column.actions': 'Actions',

  // -- Access tab: the two exceptions ---------------------------------------------
  'publish.access.extras.heading': 'Exceptions',
  'publish.access.extras.intro':
    'These two stand apart from the choice above and from each other. They let people in, and never take anyone away.',
  'publish.access.extras.moot': 'The exceptions add nothing right now',
  'publish.access.extras.mootDetail':
    'The site is public, so everyone may look anyway. Make the base above stricter to let these exceptions count; what you set here stays.',

  // -- Access tab: secret links ------------------------------------------------------
  'publish.access.keys.on': 'Secret links are on',
  'publish.access.keys.off': 'Secret links are off',
  'publish.access.keys.intro':
    'You see the full link only once, right after creating it. Revoking works immediately, also for someone who already has the page open.',
  'publish.access.keys.created': 'Secret link created',
  'publish.access.keys.createdDetail':
    'You see this link only once. Copy it now: everyone with this link can see the site, even without logging in.',
  'publish.access.keys.open': 'Open site',
  'publish.access.keys.column.label': 'Label',
  'publish.access.keys.column.created': 'Created',
  'publish.access.keys.column.expires': 'Expires',
  'publish.access.keys.column.status': 'Status',
  'publish.access.keys.empty': 'No secret links yet',
  'publish.access.keys.emptyDetail':
    'Make one below; the link can then be copied once.',
  'publish.access.keys.never': 'never',
  'publish.access.keys.active': 'Active',
  'publish.access.keys.revoked': 'Revoked',
  'publish.access.keys.revoke': 'Revoke',
  'publish.access.keys.revokeHint': 'The link stops working after that',
  'publish.access.keys.revokeFailed': 'Secret link {label} not revoked',
  'publish.access.keys.revokeFailedDetail': 'Revoking did not work.',
  'publish.access.keys.createFailed': 'Creating did not work.',
  'publish.access.keys.form.heading': 'Create a secret link',
  'publish.access.keys.form.hint':
    'The label is for yourself: it says who you shared the link with, so you know what you are revoking.',
  'publish.access.keys.form.label': 'Label',
  'publish.access.keys.form.labelHelp':
    'Handy when you hand out several links; leave it empty and the link gets a name with the date.',
  'publish.access.keys.form.expiry': 'Expires after',
  'publish.access.keys.form.days': '{days} days',
  'publish.access.keys.form.submit': 'Create secret link',

  // -- Access tab: invitees -------------------------------------------------------
  'publish.access.invitees.on': 'Invitees are on',
  'publish.access.invitees.off': 'Invitees are off',
  'publish.access.invitees.intro':
    'Invitees log in with SSO Rijk; their verified email address has to be on this list. Plak sends no invitation itself: you share the link yourself.',
  'publish.access.invitees.column.email': 'Email address',
  'publish.access.invitees.column.added': 'Added',
  'publish.access.invitees.empty': 'No invitees yet',
  'publish.access.invitees.emptyDetail': 'Add an email address below.',
  'publish.access.invitees.remove': 'Remove',
  'publish.access.invitees.removeHint': 'Cannot reach this site after that',
  'publish.access.invitees.removeFailed': '{invitee} not removed',
  'publish.access.invitees.removeFailedDetail': 'Removing did not work.',
  'publish.access.invitees.addFailed': 'Adding did not work.',
  'publish.access.invitees.form.heading': 'Add an invitee',
  'publish.access.invitees.form.hint':
    'The address that person logs in with at SSO Rijk. Capitals make no difference.',
  'publish.access.invitees.form.email': 'Email address',
  'publish.access.invitees.form.required': 'An email address',
  'publish.access.invitees.form.submit': 'Add invitee',

  // -- Access tab: external sources --------------------------------------------------
  'publish.access.external.heading': 'External sources',
  'publish.access.external.intro':
    'On unless you turn it off: the page may load scripts and styles from cdnjs, jsDelivr and unpkg, scripts from the Tailwind CDN, and fonts from Google Fonts. Those parties then see the IP address of everyone who views the page, and their code runs in the page. Turning it off is the safer choice for a confidential page. Blocked either way: fetching data from or sending it to other hosts, images from elsewhere, an iframe around the page, and a form that posts somewhere else.',
  'publish.access.external.label': 'Allow external sources',
  'publish.access.external.saved': 'External sources saved',
  'publish.access.external.on': 'External sources are on.',
  'publish.access.external.off': 'External sources are off.',
  'publish.access.external.failed': 'External sources not saved',

  // -- Access tab: shielding from other sites ----------------------------------------
  'publish.access.sandbox.heading': 'Shielding from other sites',
  'publish.access.sandbox.intro':
    'On unless you turn it off. All sites live at the same web address. While the shielding is on, the code on your pages can see nothing of the other sites at that address, and that same code can store nothing in the visitor\'s browser either: a remembered preference, a half-filled form, anything that goes through localStorage, sessionStorage or a cookie will not work. Your own styles, ordinary scripts and images load as usual. Web fonts do not, and neither does a script that fetches a file from your own site. Turn the shielding off and your site can store in the browser again, but it also shares the web address with every other site once more: the code on your pages can then reach everything the visitor is allowed to see there. Only do that for a site whose content you trust.',
  'publish.access.sandbox.label': 'Shield from other sites',
  'publish.access.sandbox.saved': 'Shielding saved',
  'publish.access.sandbox.on': 'Shielding is on.',
  'publish.access.sandbox.off': 'Shielding is off.',
  'publish.access.sandbox.failed': 'Shielding not saved',
};
