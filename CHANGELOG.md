# Changelog

## Unreleased

Add the `oni-harness` agent skill and an isolated continuous physics playground
with state-driven actions, bounded observation, fresh per-run traces, exact
repeat comparison, a self-contained HTML viewer and optional GIF/PNG rendering.
The CLI/Core v0.1.0 API and release artifacts are unchanged.

## 0.1.0 — 2026-10-19

First public preview: independent CLI/Core packages, isolated JSON and C# tests,
bounded worker output, timeout/crash handling, metadata discovery and method
contracts. A pinned Linux x64 adapter drives ONI build 744825's real native
simulation without a game or Unity Player process.

Examples cover sealed gas mass, solid heat exchange, falling liquid, custom
materials, energy injection, cell replacement, portable mod logic and one
installed game C# method. Native repeatability is compared by final state hash.

Full colony saves, duplicants/AI, prefabs, building lifecycle and Harmony patch
execution are not supported. See `docs/capabilities.md` for exact evidence limits.
