## One landing page

Typing the address of Plak itself now opens the same landing page as the admin environment. It also says where the name comes from, and without an account you can choose between Nederlands and English at the bottom.

## Service workers and the `key` parameter

Published sites can no longer register a service worker, so a PWA plugin no longer makes a site work offline. When the address of a page carries a `key` that looks like a secret link, Plak removes it before the page loads; give a query parameter of your own another name.
