---
type: decision
project: AlphaHunter
status: active
created: 2026-09-10
updated: 2026-09-10
source: hermes-senior-developer
---

# AlphaHunter AI, design direction

Direction written by the agent at the user's request ("lightweight, constrained server"). It is
agent-authored, so it can drift toward default taste. Override any line here and the UI follows.

## Identity

An operator console for one person watching an autonomous paper-trading bot. It is a tool, not
a marketing page: dense, quiet, and legible at a glance from a phone. It should read like
instrumentation, not like a product landing page.

## Dial

```
ENERGY 1 / RHYTHM 2 / MOTION 1
```

- ENERGY 1: nothing shouts. Attention comes from position and type scale, not decoration.
- RHYTHM 2: mostly uniform panels, with deliberate breaks where the content wants it (the
  metrics strip, the wide decision table, the paired learning panels).
- MOTION 1: no entrance animation, no parallax. Only a visible focus ring and the dashboard
  auto refresh. `prefers-reduced-motion` respected.

## Palette

| Token | Value | Role |
|---|---|---|
| `--bg` | `#0e1114` | page. Dark because this is a monitoring tool read for long stretches, and because the product is about numbers rather than imagery. |
| `--panel` | `#151a1f` | panels and cards. |
| `--accent` | `#e0a33e` | amber. Marks the one thing that matters per screen: the active nav item, the primary action, the bar in a score breakdown. |
| `--pos` / `--neg` | `#5fbc7d` / `#e0705c` | semantic only, never decorative. Profit and loss must be distinguishable without reading the number. |

Neutrals plus one accent. No gradients anywhere: a gradient here would carry no information.

## Typography

System UI stack for prose, monospace with `tabular-nums` for every number. Reason: columns of
prices and percentages must align to be scannable, and that is the only place monospace earns
its keep. No oversized terminal type as decoration.

## Identity motif

Two repeated gestures, both functional:

1. **Tabular numerals everywhere.** Numbers line up across rows so the eye can compare without
   reading labels.
2. **Risk spine.** Each position draws a horizontal band from its initial stop to its second
   take-profit, with a marker at the current price. It answers "how close is this to my stop"
   without arithmetic.

## States

Empty, loading and error states are part of the design, not an afterthought (R-27). Empty states
say what would have to happen for data to appear. The action error banner is a live region.
