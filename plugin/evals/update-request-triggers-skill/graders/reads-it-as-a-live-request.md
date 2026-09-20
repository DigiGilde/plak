---
type: llm
---

The user asked to update their existing site ("werk mijn site bij").

PASS if the answer treats this as a request to replace the live site, for
example by planning a publish without a preview ref, by naming the live base
path, or by confirming with the user that the live site will be replaced.
PASS as well if the answer proposes a preview first but states plainly that
the user asked for live and asks for confirmation.
FAIL if the answer silently turns the request into a preview only, with no
mention that the user asked to update the live site, or if it refuses the
request outright.
