## Changes per version

Plak now has a "What's new" page, reachable through the link at the bottom of every page. There you read what changed in each version. The bottom of the page also shows the version of Plak you are using now.

## The CLI

`plak --version` shows which version of the Plak CLI you use. If the server speaks a newer version of the API than the CLI knows, the CLI stops and tells you how to update.

The CLI now also runs on Windows; `plak login` keeps your session in Windows Credential Manager. `plak publish` ends with the URL and the version you just published. When `plak site link` cannot find your repository, the CLI says why and what you can do.

## Previews on the pull request

The publish action can now show a preview on its pull request on GitHub: as a deployment with a "View deployment" button, as a comment with the link, or both. The comment says who can see the preview and whether opening it needs a sign-in. Pull requests opened by bots, such as Dependabot, are skipped by default. After publishing, the log and the job summary show the URL and the version.

The action is now `actions/publish` instead of `actions/publiceer`, and for the DigiGilde instance you no longer need to give `host`. When you move the pinned commit in your workflow, change the path too.

## Older versions are cleaned up

By default Plak keeps the current live version of a site and the five versions before it; a site administrator can set a different number per site. Older live versions are removed every night, so a site that publishes often no longer runs out of room. The "Versions" tab shows how much of its space the site uses and how many versions are kept; that is also where a site administrator sets the number.

## Unconfirmed repository

If you linked a private repository by entering its repository ID and owner ID yourself, the "Deploy" tab says "Not confirmed yet". The first publish from that repository confirms the IDs and corrects the name. When a repository is renamed, Plak takes over the new name with the next publish.

## Content volume

As a platform administrator you now see how full the content volume is on the "Platform administration" page: used, free and the reserve. It turns red when a deploy of the maximum size would no longer fit.

## API documentation in two languages

The API documentation, linked at the bottom of every page, is now in English and in Dutch. It follows the language on your profile when you are signed in, and the switch at the top of the page changes it.
