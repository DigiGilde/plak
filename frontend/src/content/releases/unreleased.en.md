## One landing page

Typing the address of Plak itself now opens the same landing page as the admin environment. It also says where the name comes from, and without an account you can choose between Nederlands and English at the bottom.

## Service workers and the `key` parameter

Published sites can no longer register a service worker, so a PWA plugin no longer makes a site work offline. When the address of a page carries a `key` that looks like a secret link, Plak removes it before the page loads. Does your site use a `key` parameter of its own? Then give it another name.

## A workflow names the site ID

A workflow that publishes from GitHub or Forgejo now names the ID of its site: `site-id` for the action, `--site-id` for the CLI. That way the content never ends up at another site, not through a typo in the address and not through a site that was deleted and created again. The ID is on the "Deploy" tab, with a button to copy it, and in the ready-made workflow there. Does your workflow use an older version of the action or the CLI? Then update it first, because it does not know `site-id` yet.

A new link asks for the site ID from the start. Existing links keep working without it, unless the same repository is linked to more than one site: then every link of that repository requires the site ID at once. A site administrator can choose "Only publish with the site ID" on the "Deploy" tab. That cannot be undone.

## Access without repetition

Under the access choice you no longer read again what you just chose. When nobody can view the site, a warning says so. For the default access of a group, secret links and invitees now sit under "Exceptions", just like on the "Access" tab of a site.
