/**
 * English counterpart of pages.nl.ts. Typed against it, so a key present in
 * one language and missing from the other is a compile error rather than a
 * raw key on screen.
 */
import type { pagesNl } from './pages.nl';

export const pagesEn: Record<keyof typeof pagesNl, string> = {
  // -- Landing: access withdrawn -------------------------------------------
  'page.landing.withdrawn.title': 'Your access has been withdrawn',
  'page.landing.withdrawn.intro':
    'You are signed in with your organisation account, but Plak no longer lets you in.',
  'page.landing.withdrawn.body':
    'Your account is still there, with everything attached to it; only your access is closed. If you do not know why, ask the platform administrator of your organisation. They can give your access back as well.',
  'page.landing.withdrawn.check': 'Check again',
  'page.landing.withdrawn.failed.title': 'Check failed',
  'page.landing.withdrawn.failed.detail':
    'Plak cannot be reached right now. Try again in a moment.',

  // -- Landing: no session --------------------------------------------------
  'page.landing.intro':
    'Share an HTML page quickly and simply. A report, an analysis or an overview, written by you or with an AI assistant: put it on Plak and share the link. You decide who may see it.',
  'page.landing.loginHint': 'Sign in with your Rijksoverheid account to get started.',
  'page.landing.login': 'Sign in',
  'page.landing.steps.heading': 'How to share a page',
  'page.landing.steps.upload':
    'Put your HTML file on Plak by uploading it, or let your AI assistant do it for you.',
  'page.landing.steps.audience':
    'Choose who may see it: everyone, colleagues who sign in, only the people you invite, or whoever has the secret link.',
  'page.landing.steps.share': 'Share the link.',
  'page.landing.steps.update':
    'To change something, upload a new version. Your colleagues always see the latest one, and older versions stay available.',
  'page.landing.site.heading': 'For a whole site as well',
  'page.landing.site.body':
    'Working on a site with several pages in a repository? Then publish from GitHub or code.overheid.nl, with a preview per pull request.',

  // -- Start ----------------------------------------------------------------
  'page.start.loading': 'Loading...',

  // -- About ----------------------------------------------------------------
  'page.about.title': 'About Plak',
  'page.about.intro':
    'Plak is there for that one page you want to share. A report, an analysis, an overview: you put it online, choose who may see it and share the link. Not an attachment drifting through mailboxes, but an address you can update and withdraw again.',
  'page.about.features.heading': 'What you can do with it',
  'page.about.features.share.term': 'Share a page.',
  'page.about.features.share.body':
    'Put your HTML file on Plak and you have an address to share.',
  'page.about.features.update.term': 'Update without a new address.',
  'page.about.features.update.body':
    'A new version goes over the previous one; everyone with the link sees the latest one automatically. Older versions are kept.',
  'page.about.features.audience.term': 'Decide who gets to look.',
  'page.about.features.audience.body':
    'From public to invited people only, and everything in between. You can share a secret link in one go, or send the link and the code separately.',
  'page.about.features.revoke.term': 'Withdraw.',
  'page.about.features.revoke.body':
    'A link that has been passed around too far, you withdraw. After that it does nothing.',
  'page.about.features.site.term': 'A whole site as well.',
  'page.about.features.site.body':
    'If you work in a repository, you publish from GitHub or code.overheid.nl, with a preview per pull request.',
  'page.about.features.cli.term': 'From your own computer.',
  'page.about.features.cli.body':
    'With the Plak CLI you publish from your laptop, once you have linked it to your account.',
  'page.about.name.heading': 'Did you know',
  'page.about.name.body':
    'The name comes from plakkaat, Dutch for a placard: an announcement you put up nearby so that others can see it. A placard can hang in a public space, visible to everyone, or in a closed room only a small group enters. That is exactly what Plak does.',

  // -- Privacy --------------------------------------------------------------
  'page.privacy.title': 'Privacy',
  'page.privacy.intro':
    'Plak processes personal data to arrange access to published sites: an e-mail address when you sign in through your organisation, and possibly the e-mail address of an invited person when access is by invitation.',
  'page.privacy.data.heading': 'Which data',
  'page.privacy.data.account':
    'Account data: name, e-mail address and organisation, through signing in.',
  'page.privacy.data.audit':
    'Audit data: who published or changed what and when, and who signed in when.',
  'page.privacy.data.visits':
    'Visit data: which pages of a restricted site you looked at. Nothing is recorded for public sites.',
  'page.privacy.retention.heading': 'Retention period',
  'page.privacy.retention.body':
    'Visit data and sign-in moments are deleted after 90 days. Other audit data, such as refused access and administrative actions, is kept for three years, because it may become part of the investigation into a security incident. Only a platform administrator can view it, and every viewing is itself recorded, with a mandatory reason that every platform administrator can read. Your name and e-mail address are not in there in readable form. Previews and their access data expire automatically after thirty days without a new deploy.',
  'page.privacy.retention.ip':
    'Every audit entry also carries the IP address something happened from: a truncated network (not the full address) is simply in the log, kept as long as the entry itself. The full address sits beside it, encrypted, kept just as long, and only a platform administrator can request it, with the same mandatory, recorded reason.',
  'page.privacy.retention.session':
    'If you view restricted content after signing in with your organisation, Plak keeps your SSO ID and e-mail address until ninety days after the last time you signed in. That is needed to trace an entry in the audit log back to a person, for instance during the investigation into a leak.',
  'page.privacy.visibility.heading': 'Who can see what',
  'page.privacy.visibility.body':
    'The audit log can only be viewed by a platform administrator. The entries hold no name and no e-mail address, but a pseudonym: an encrypted representation of your SSO ID. A platform administrator can look up which pseudonym belongs to a person, and the other way round who is behind a pseudonym. That is only possible with a stated reason, which is itself recorded, and there is a maximum number of lookups per day. The administrator of a site sees none of this.',
  'page.privacy.rights.heading': 'Your rights',
  'page.privacy.rights.bodyBeforeEmail':
    'You may request which data Plak processes about you, have it corrected, and object to the processing. Deletion is not always possible: the audit log is fixed for as long as the retention period runs, because it serves to make misuse investigable. Ask your question at',
  'page.privacy.rights.bodyAfterEmail':
    '; you will get a reply within ten working days. If you disagree with what we do, you can lodge a complaint with the Dutch Data Protection Authority.',

  // -- Accessibility --------------------------------------------------------
  'page.accessibility.title': 'Accessibility',
  'page.accessibility.intro':
    'We think it matters that Plak works well for everyone. Plak aims for WCAG 2.1, level AA, as the Dutch decree on digital accessibility of government (EN 301 549) requires. The admin interface is built with the NLDD Design System, which builds accessibility into the components: ARIA, focus order and keyboard operation.',
  'page.accessibility.works.heading': 'What already works',
  'page.accessibility.works.keyboard': 'Everything can be operated with the keyboard.',
  'page.accessibility.works.skipLink': 'Every page starts with a link to the main content.',
  'page.accessibility.works.colorScheme':
    'The interface follows your preference for a light or dark screen.',
  'page.accessibility.works.checks':
    'Automated accessibility checks run along with every change.',
  'page.accessibility.todo.heading': 'What is not finished yet',
  'page.accessibility.todo.body':
    'Plak is a beta version and has not been audited yet. So there is no statement in the national register at toegankelijkheidsverklaring.nl either; that will follow as soon as Plak goes into production. Pages that you or your colleagues publish belong to their makers: Plak does not check their accessibility.',
  'page.accessibility.report.heading': 'Reporting a problem',
  'page.accessibility.report.bodyBeforeEmail':
    'Did you run into something that does not work, or do you have an idea for an improvement? Get in touch at',
  'page.accessibility.report.bodyAfterEmail':
    '. You will get a reply within ten working days. We work on improving accessibility continuously.',

  // -- Linking a CLI session (plak login) -----------------------------------
  'page.cliPair.loading': 'Loading',
  'page.cliPair.error.unknownCode':
    'This code is unknown, expired or already used. Start plak login again.',
  'page.cliPair.error.tooManyAttempts': 'Too many attempts. Try again in a moment.',
  'page.cliPair.unknownClient': 'Unknown program',
  'page.cliPair.login.intro': 'You can only link a CLI session shortly after signing in yourself.',
  'page.cliPair.login.afterwards':
    'Sign in, then copy in the code that plak login shows in your terminal.',
  'page.cliPair.login.action': 'Sign in',
  'page.cliPair.codeEntry.intro':
    'Copy in the code your terminal shows to link plak login to your account.',
  'page.cliPair.codeEntry.install.before': 'No Plak CLI yet? ',
  'page.cliPair.codeEntry.install.link': 'Here is how to install and use it.',
  'page.cliPair.codeEntry.label': 'Code from your terminal',
  'page.cliPair.codeEntry.placeholder': 'ABCD-EFGH',
  'page.cliPair.codeEntry.required': 'The code from your terminal, in the form ABCD-EFGH',
  'page.cliPair.codeEntry.submit': 'Look up code',
  'page.cliPair.confirm.question': 'Does your terminal show the same code?',
  'page.cliPair.confirm.account': 'You are linking this program to {account}',
  'page.cliPair.confirm.accountHint':
    'After that the program can publish with all of your roles, until you unlink it.',
  'page.cliPair.confirm.client': 'Program, as it calls itself: "{name}"',
  'page.cliPair.confirm.requested': 'Requested: {time}',
  'page.cliPair.confirm.network': 'From network {network}',
  'page.cliPair.confirm.otherNetwork.title':
    'This request comes from a different network than the one you are on now',
  'page.cliPair.confirm.otherNetwork.detail':
    'If plak login runs on another machine or over a VPN, that can be right. If you are in doubt, click Refuse.',
  'page.cliPair.confirm.warning.title':
    'Only link if you started plak login yourself just now',
  'page.cliPair.confirm.warning.detail':
    'Did someone send you this link or code? Then click Refuse.',
  'page.cliPair.confirm.approve': 'Link',
  'page.cliPair.confirm.deny': 'Refuse',
  'page.cliPair.approved.title': 'Linked',
  'page.cliPair.approved.detail': 'You can go back to your terminal.',
  'page.cliPair.denied.title': 'Refused',
  'page.cliPair.denied.detail': 'The program gets no access.',

  // -- Done: the result screen after publishing -----------------------------
  'page.done.loading': 'Loading the result…',
  'page.done.heading.online': 'Your site is online',
  'page.done.heading.offline': 'Your site is not online yet',
  'page.done.error.unknownSite.title': 'Unknown site',
  'page.done.error.unknownSite.detail': 'No site "{site}" in group "{group}".',
  'page.done.address.site': 'Address of your site',
  'page.done.copy.button': 'Copy address',
  'page.done.copy.ok': 'Address copied.',
  'page.done.copy.failed':
    'Copying did not work. Select the address above and copy it yourself.',
  'page.done.open': 'Open site',
  'page.done.published.title': 'Published: {title}',
  'page.done.published.detail': 'Last published: {time}',
  'page.done.noVersion.title': 'No version is online yet',
  'page.done.noVersion.detail':
    'The site exists, but nothing has been published yet. Publish a version on the site page.',
  'page.done.noVersion.action': 'To the site',
  'page.done.visibility.heading': 'Who can see this',
  'page.done.visibility.change': 'Change who can see this',
  'page.done.key.created.title': 'Secret link created',
  'page.done.key.created.detail':
    'You see this link only once. Copy it now: anyone with this link can view the site, even without signing in. You can withdraw the link on the Access tab.',
  'page.done.key.failed.title': 'Secret link not created',
  'page.done.key.failed.fallback': 'Creating it did not work.',
  'page.done.key.failed.action': 'To Access',
  'page.done.next.heading': 'Next',
  'page.done.next.site': 'Publish a new version or manage this site',
  'page.done.next.overview': 'Back to the overview',
};
