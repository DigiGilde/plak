## Plak is now Toon

Plak has a new name: Toon. The name comes from the Dutch word tonen, to show. Toon shows your page or site to whoever may see it. That can be everyone, or only a small group.

- The addresses are `toon.rijks.app` and `beheer.toon.rijks.app`. The old ones redirect, so links keep working. Did a visitor come in through a secret link? They open the full link once more.
- Sign in again once.
- Is the old address in your site, for example in `canonical`, `og:url` or a fixed path? Then publish the site again with `https://toon.rijks.app`. Styles and scripts do not load through the old address.
- Do you publish with the CLI? The command is now `toon`. Remove the old one with `uv tool uninstall plak` and install the new one with `uv tool install "git+https://github.com/DigiGilde/toon@beta#subdirectory=cli"`. "Linked CLI sessions" is empty, so then run `toon login` on each computer you use the CLI on.
- Do you publish from a workflow? Then change two lines in every step that uses the action. `uses:` becomes `DigiGilde/toon/actions/publish@<commit-sha>`, with the commit of a release under the name Toon. `host:` becomes `https://beheer.toon.rijks.app`. The "Deploy" tab of a site shows the new workflow.
- Do you use the plugin in Claude Code? Then run `/plugin marketplace remove plak`, `/plugin marketplace add DigiGilde/toon` and `/plugin install toon@toon`.

The section [Moving over from Plak](https://github.com/DigiGilde/toon/blob/beta/docs/publishing.md#9-moving-over-from-plak) has the details.
