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
  'page.whatsNew.title': "What's new in Plak",
  'page.whatsNew.empty': 'No releases yet.',
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
  'page.privacy.title':
    'Privacy',
  'page.privacy.intro':
    'This statement describes how Plak handles personal data and which rights you have. We work under the General Data Protection Regulation (GDPR, in Dutch the AVG). Plak processes personal data to arrange access to published sites: an e-mail address when you sign in through your organisation, and possibly the e-mail address of an invited person when access is by invitation.',
  'page.privacy.draft':
    'This is a draft. This statement has not been legally reviewed yet, not even by the Data Protection Officer.',
  'page.privacy.controller.heading':
    'Who is responsible',
  'page.privacy.controller.body':
    'The Digi Gilde, which builds and runs Plak, is part of the Rijksorganisatie voor Ontwikkeling, Digitalisering en Innovatie (ODI), which falls under the Ministry of the Interior and Kingdom Relations (BZK). The minister and the state secretary of BZK are the controller of the personal data processed through Plak.',
  'page.privacy.data.heading':
    'Which data',
  'page.privacy.data.account':
    'Account data: name and e-mail address, through signing in, and your role in a group or site.',
  'page.privacy.data.invitees':
    'Invitees: the e-mail address or SSO ID of whoever an administrator puts on the list of a site, even if that person never signs in.',
  'page.privacy.data.audit':
    'Audit data: who published or changed what and when, and who signed in when.',
  'page.privacy.data.visits':
    'Visit data: which pages of a restricted site you looked at. Nothing is recorded for public sites.',
  'page.privacy.data.content':
    'Published content: may contain personal data, but that belongs to the maker of the site. Plak does not read, classify or filter that content; the publishing organisation is responsible for it.',
  'page.privacy.purpose.heading':
    'Purpose and legal basis',
  'page.privacy.purpose.body':
    'We process this data to show published sites only to those who may see them, and to keep the platform secure and misuse investigable. The legal basis is article 6(1)(e) of the GDPR: the performance of a task carried out in the public interest.',
  'page.privacy.processors.heading':
    'Processors and hosting',
  'page.privacy.processors.body':
    'Plak runs on ZAD, the hosting platform of the Dutch central government, using the database and file volume of that platform. Signing in goes through the Keycloak of ZAD, which forwards to SSO Rijk. Plak uses no web analytics and shares no data with other parties. The agreements with the hosting platform are still to be put in writing.',
  'page.privacy.retention.heading':
    'Retention period',
  'page.privacy.retention.body':
    'Visit data and sign-in moments are deleted after 90 days. Other audit data, such as refused access and administrative actions, is kept for three years, because it may become part of the investigation into a security incident. Only a platform administrator can view it, and every viewing is itself recorded, with a mandatory reason that every platform administrator can read. Your name and e-mail address are not in there in readable form. Previews and their access data expire automatically after thirty days without a new deploy.',
  'page.privacy.retention.ip':
    'Every audit entry also carries the IP address something happened from: a truncated network (not the full address) is simply in the log, kept as long as the entry itself. The full address sits beside it, encrypted, kept just as long, and only a platform administrator can request it, with the same mandatory, recorded reason.',
  'page.privacy.retention.session':
    'If you view restricted content after signing in with your organisation, Plak keeps your SSO ID and e-mail address until ninety days after the last time you signed in. That is needed to trace an entry in the audit log back to a person, for instance during the investigation into a leak.',
  'page.privacy.visibility.heading':
    'Who can see what',
  'page.privacy.visibility.body':
    'The audit log can only be viewed by a platform administrator. The entries hold no name and no e-mail address, but a pseudonym: an encrypted representation of your SSO ID. A platform administrator can look up which pseudonym belongs to a person, and the other way round who is behind a pseudonym. That is only possible with a stated reason, which is itself recorded, and there is a maximum number of lookups per day. The administrator of a site sees none of this.',
  'page.privacy.rights.heading':
    'Your rights',
  'page.privacy.rights.body':
    'You have the right to access your data, to have it corrected or deleted, and to object to or ask for restriction of the processing. Deletion is not always possible: the audit log is fixed for as long as the retention period runs, because it serves to make misuse investigable.',
  'page.privacy.rights.request.before':
    'Send a request to the Ministry of the Interior and Kingdom Relations through the ',
  'page.privacy.rights.request.link':
    'contact form of the Dutch central government',
  'page.privacy.rights.request.after':
    ', or by post: Ministerie van Binnenlandse Zaken en Koninkrijksrelaties, Postbus 20011, 2500 EA Den Haag.',
  'page.privacy.rights.register.before':
    'The processing operations of the central government are listed in the ',
  'page.privacy.rights.register.link':
    'AVG register of the Rijksoverheid',
  'page.privacy.rights.register.after':
    '.',
  'page.privacy.fg.heading':
    'Data Protection Officer',
  'page.privacy.fg.before':
    'The Ministry of the Interior and Kingdom Relations has a Data Protection Officer (in Dutch the Functionaris Gegevensbescherming, FG) who supervises compliance with the GDPR. You can reach the DPO at ',
  'page.privacy.fg.after':
    '.',
  'page.privacy.complaint.heading':
    'Lodging a complaint',
  'page.privacy.complaint.before':
    'Do you disagree with how we handle your data? You can lodge a complaint with the ',
  'page.privacy.complaint.link':
    'Dutch Data Protection Authority (Autoriteit Persoonsgegevens)',
  'page.privacy.complaint.after':
    '.',
  'page.privacy.contact.heading':
    'Contact',
  'page.privacy.contact.before':
    'Do you have a question about this statement or about Plak? Mail us at ',
  'page.privacy.contact.after':
    '; you will get a reply within ten working days. Privacy requests and complaints go through the addresses above, so that they reach the right place within the ministry.',

  // -- Accessibility --------------------------------------------------------
  'page.accessibility.title':
    'Accessibility',
  'page.accessibility.intro':
    'This statement describes to what extent Plak meets the accessibility requirements for government websites. The legal norm is WCAG 2.1 level AA, through EN 301 549 and mandatory under the Besluit digitale toegankelijkheid overheid (the Dutch decree on digital accessibility of government). It concerns the admin interface of Plak; pages that you or your colleagues publish belong to their makers, and Plak does not check their accessibility.',
  'page.accessibility.draft.before':
    "This is a draft. The status below rests on the team's own tests. An investigation by an independent party has not taken place. The final statement still has to be drawn up with the form assistant at ",
  'page.accessibility.draft.link':
    'toegankelijkheidsverklaring.nl',
  'page.accessibility.draft.after':
    ' and published in the register there. Until that is done, this is not a legally valid statement.',
  'page.accessibility.status.heading':
    'Compliance status',
  'page.accessibility.status.body':
    'Status C in the DigiToegankelijk model: the accessibility of Plak has not been fully investigated yet. It has been tested (see below), but there is no complete, documented investigation against all success criteria of WCAG 2.1 AA yet.',
  'page.accessibility.tested.heading':
    'How this was tested',
  'page.accessibility.tested.components':
    'The admin interface is built from the components of the NLDD Design System. They bring their own keyboard behaviour, focus indication, colour contrast and ARIA attributes. Every page starts with a link to the main content and the interface follows your preference for a light or dark screen.',
  'page.accessibility.tested.automated':
    'Automated, with every change: the admin tests run axe-core on the group page, the site page and the profile, in Dutch and in English, and a change with a violation fails. The language of the page is set on the html element, so that a screen reader picks the right pronunciation.',
  'page.accessibility.tested.notDone':
    'Not done: an investigation by an independent party, a test with screen readers per page and a full pass through all success criteria of WCAG 2.1 AA.',
  'page.accessibility.limits.heading':
    'Known limitations',
  'page.accessibility.limits.contrast':
    'Colour contrast is not measured in the automated tests, because the test environment has no rendering. The contrast rests on the colours of the design system.',
  'page.accessibility.limits.pages':
    'Not every page has an automated test of its own; the coverage is not complete yet.',
  'page.accessibility.limits.shadow':
    'The components of the design system draw landmarks and lists, among other things, in their shadow DOM. Support for that differs per screen reader and has not been verified per screen reader.',
  'page.accessibility.limits.published':
    'Published sites fall outside this statement: their makers are responsible for the accessibility of what they publish.',
  'page.accessibility.report.heading':
    'Reporting an accessibility problem',
  'page.accessibility.report.bodyBeforeEmail':
    'Did you run into something that does not work, or do you have a question about accessibility? Let us know at',
  'page.accessibility.report.bodyAfterEmail':
    '. You will get a reply within ten working days. We work on improving accessibility continuously.',
  'page.accessibility.enforcement.heading':
    'Enforcement',
  'page.accessibility.enforcement.before':
    'Are you not satisfied with how we handle your report, or do you get no reply? Then you can lodge a complaint with the ',
  'page.accessibility.enforcement.link':
    'College voor de Rechten van de Mens (the Netherlands Institute for Human Rights)',
  'page.accessibility.enforcement.after':
    '.',
  'page.accessibility.prepared':
    'This statement was prepared on 7 October 2026.',

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
