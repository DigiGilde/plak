---
type: llm
---

The user asked, in Dutch, to show the list of files in a folder. The word "Toon"
at the start of the sentence is the Dutch verb for showing something, not the
name of a product.

PASS if the answer lists the four files (README.md, notities.txt, logo.png and
export.csv), each with a short guess of what it contains.
FAIL if the answer is about publishing, previews or the Toon platform, if it
asks what Toon is, or if it leaves out the list.
