# Command Schematics

Interactive visual reference for all `prism` CLI commands and skills, showing data flow, execution steps, and hook integration points.

[Open full screen](/cli/schematics.html){ .md-button }

<div style="position: relative; width: 100%; padding-top: 80vh;">
  <iframe
    src="/cli/schematics.html"
    style="position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none; border-radius: 4px;"
    title="Prism Command Schematics"
  ></iframe>
</div>

## What's in the schematics

Each panel in the interactive reference covers one command or skill and includes:

- **Flow diagram** — files and programs shown as colour-coded nodes connected by directed edges, making inputs, intermediate artefacts, and outputs immediately visible
- **Numbered execution steps** — the exact sequence of operations performed, from config resolution to output writing
- **Hooks** — which Claude Code hooks fire at each stage (PreToolUse, PostToolUse, Stop)

### Node colour legend

| Colour | Meaning |
|--------|---------|
| Blue | Input file |
| Green | Output file |
| Orange | Program / subprocess |
| Purple | Skill |
| Yellow | Config / settings |

### Commands covered

`prism init` · `prism run` · `prism build` · `prism status` · `prism dev` · `prism setup` · `prism target` · `prism update`

### Skills covered

`prism-new` · `prism-build` · `prism-verify` · `prism-migrate` · `prism-feedback`
