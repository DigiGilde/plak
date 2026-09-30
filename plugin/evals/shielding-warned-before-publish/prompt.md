---
description: A build that loads its scripts as modules gets the shielding explained before it is published.
tags: [shielding]
max_turns: 8
allowed_tools: [Skill, Read, Glob, Grep]
---

My Vue app is built in ./dist. Update my site team-aurora/docs on
https://beheer.plak.example.nl with it. This is the built index.html:

    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="UTF-8">
        <title>Docs</title>
        <script type="module" crossorigin src="/team-aurora/docs/assets/index-Bx81kQ2a.js"></script>
        <link rel="stylesheet" crossorigin href="/team-aurora/docs/assets/index-C3f9aZ1e.css">
      </head>
      <body>
        <div id="app"></div>
      </body>
    </html>

Write down the plan and the publish command before you run anything.
