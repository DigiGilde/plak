## Changes per version

Plak now has a "What's new" page, reachable through the link at the bottom of every page. There you read what changed in each version.

The bottom of the page also shows the version of Plak you are using now.

## Version of the CLI

`plak --version` shows which version of the Plak CLI you use. If the server speaks a newer version of the API than the CLI knows, the CLI stops and tells you how to update.

## Previews on the pull request

The `publiceer` action can now show a preview on its pull request on GitHub: as a deployment with a "View deployment" button, as a comment with the link, or both. Pull requests opened by bots, such as Dependabot, are skipped by default.
