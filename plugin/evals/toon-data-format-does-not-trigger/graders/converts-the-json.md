---
type: llm
---

The user asked to convert JSON to TOON, the data format (Token-Oriented Object
Notation). The Toon publishing platform has nothing to do with this.

PASS if the answer gives the JSON as TOON: an indentation-based notation without
braces around the objects, for example a header such as `gebruikers[2]{id,naam,rol}:`
followed by one row of values per user.
FAIL if the answer is about publishing a site, previews or the Toon platform, if
it asks what TOON is, or if it gives no conversion.
