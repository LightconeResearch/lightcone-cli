# Prism UI Brand

Visual patterns for user-facing Prism output. Skills reference this file for consistent formatting.

The principle: use markdown idiomatically. Minimal Unicode decoration. Let the content breathe.

## Stage Banners

Use for major phase transitions. A markdown `###` heading.

```
### Research Question
```

**Stage names:**
- `Research Question`
- `Analysis Structure`
- `Deep Dive — [Section]`
- `Finalizing`
- `Specification Complete`
- `Verify — <universe_id>`
- `Verification Report — <universe_id>`

---

## Status Symbols

```
✓  Complete / Passed
○  Pending
✗  Failed
```

---

## Action Prompts

When user input is needed, use bold text:

```
**What are you trying to learn? Describe the research question in your own words.**
```

No boxes, no rules, no blockquotes (those render grey/dimmed).

---

## Next Up Block

At end of major completions:

```
---
**Next up**

**{Description}** — {one-line detail}

`{copy-paste command}`

Also available: `/prism-verify`, ...
```

---

## Tables

```
| Section | Decisions | Outputs |
|---------|-----------|---------|
| main    | 3         | 2       |
```

---

## Anti-Patterns

- Heavy Unicode box-drawing (━, ─── rules, ► arrows)
- Boxing prompts in horizontal rules
- Random emoji
- Walls of decoration that distract from content
- Missing Next Up block after completions
