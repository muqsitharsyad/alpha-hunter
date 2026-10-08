# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

A single private operator monitoring a paper-only crypto smart-money system through a Tailscale-protected browser UI. The operator needs to understand risk, current positions, decision evidence, and system health quickly on desktop or phone.

## Product Purpose

AlphaHunter discovers smart-money signals, qualifies opportunities through safety and feasibility gates, and manages simulated paper positions. The interface makes the system auditable rather than presenting investment advice or a live-trading promise.

## Positioning

A private paper-trading operator console where every candidate, gate, simulated fill, and exit has an explainable record. It has no live-money execution path.

## Operating Context

The operator checks the dashboard repeatedly during monitoring, reads dense tables, adjusts risk limits deliberately, and investigates positions, opportunities, provider health, alerts, and audit history. During maintenance and soak acceptance the bot remains paused.

## Capabilities and Constraints

FastAPI, Jinja2, vanilla JavaScript, and SQLite WAL. One lightweight non-root container on a constrained home server. Private no-login browser UI is restricted to Tailscale production origins; JSON API remains bearer-protected. Unknown data must not be rendered as zero. Real market edge, profitability, and live readiness are not established.

## Brand Commitments

Dark, data-first operator console. The established visual direction uses a restrained near-black palette, one amber accent, semantic green/red only for real outcomes, system UI for prose, and tabular numerals for numerical comparison. No gradients, stock imagery, decorative icons, invented statistics, or marketing claims.

## Evidence on Hand

The application itself supplies live operational data, tables, risk events, and audit records. No customer testimonials, benchmark claims, or external brand assets are available or permitted to be fabricated.

## Product Principles

- Show the operator the next material decision before secondary detail.
- Keep paper-only status and risk boundaries legible at all times.
- Prefer explainable evidence and honest empty states to decorative activity.
- Keep dense information readable across desktop, tablet, and phone.
- Preserve working forms, navigation, and API security while changing presentation.

## Accessibility & Inclusion

WCAG AA contrast, visible keyboard focus, meaningful loading, empty, and error states, and responsive layouts that do not rely on hover or horizontal page scrolling.
