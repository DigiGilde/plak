## A workflow names the site ID

A workflow that publishes from GitHub or Forgejo now names the ID of its site: `site-id` for the action, `--site-id` for the CLI. That way the content never ends up at another site, not through a typo in the address and not through a site that was deleted and created again. The ID is on the "Deploy" tab, with a button to copy it, and in the ready-made workflow there.

A new link asks for the site ID from the start. Existing links keep working without it, unless the same repository is linked to more than one site: then every link of that repository requires the site ID at once. A site administrator can choose "Only publish with the site ID" on the "Deploy" tab. That cannot be undone.
