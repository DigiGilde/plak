---
type: llm
---

The user wants to publish a Vite-built Vue app whose index.html loads the whole
app through `<script type="module" crossorigin src=...>`. On Plak every site is
shielded from the other sites by default, and a shielded page cannot load a
module script from its own site, so this app would show a blank page.

PASS if, before or alongside the publish command, the answer warns that the
module script will be refused while "Afschermen van andere sites" (the
shielding) is on, and says that turning it off is a choice for the site's
admin in the admin environment, with the trade-off that the site then shares
its web address with the other sites again.
FAIL if it does not mention the shielding or the module script problem, if it
claims the page will simply work, if it proposes to turn the shielding off
itself or through the CLI, or if it proposes a CORS or server setting as the
fix.
