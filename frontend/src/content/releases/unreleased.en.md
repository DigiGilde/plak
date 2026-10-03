## Changes per version

Plak now has a "What's new" page, reachable through the link at the bottom of every page. There you read what changed in each version.

The bottom of the page also shows the version of Plak you are using now.

## Version of the CLI

`plak --version` shows which version of the Plak CLI you use. If the server speaks a newer version of the API than the CLI knows, the CLI stops and tells you how to update.

## Previews on the pull request

The publish action can now show a preview on its pull request on GitHub: as a deployment with a "View deployment" button, as a comment with the link, or both. Pull requests opened by bots, such as Dependabot, are skipped by default. For the DigiGilde instance you no longer need to give `host`.

The action is now `actions/publish` instead of `actions/publiceer`. When you move the pinned commit in your workflow, change that path too.

## Older versions are cleaned up

By default Plak keeps the current live version of a site and the five versions before it; a site administrator can set a different number per site. Older live versions are removed every night, so a site that publishes often no longer runs out of room. The "Versions" tab shows how much space the site uses and how many versions are kept; that is also where a site administrator sets the number.

## The CLI on Windows

The Plak CLI now also runs on Windows. `plak login` keeps your session in Windows Credential Manager.

## Content volume

As a platform administrator you now see how full the content volume is on the "Platform administration" page: used, free and the reserve. It turns red when a deploy of the maximum size would no longer fit.
