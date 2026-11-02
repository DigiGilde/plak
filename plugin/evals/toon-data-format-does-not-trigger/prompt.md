---
description: The TOON data format must not pull in the publishing skill.
tags: [triggering]
max_turns: 8
allowed_tools: [Skill, Read, Glob, Grep]
---

Zet deze JSON om naar TOON, zodat het minder tokens kost in mijn prompt:

    {
      "gebruikers": [
        {"id": 1, "naam": "Anna", "rol": "admin"},
        {"id": 2, "naam": "Bram", "rol": "lezer"}
      ]
    }
