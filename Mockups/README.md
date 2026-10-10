# Sera UI/UX Mockups

Mockups for five screens: All Clients / Search, the client detail panel, the audit log, Sera Sync and Settings. Nothing in the app has changed — these are proposals to review first. Colours are the app's own (#202020 page, #141414 panels, #2E9B5F emerald, #4CF9B7 mint, white data grid). Manage Clients, Tracker Dump and the display scale picker joined the overhaul on 2026-10-10 (phase 4); their specs (07-09) are written first by work package W4-1. The SGT lab and the SDIS panels are out of the overhaul.

Live, clickable version (all 6 tabs in one page): https://claude.ai/artifact/1G4UtGzSgDXkzP7hp6s6R5

## Screens

- [All Clients / Search](01-all-clients-search.md)
- [Client detail panel](02-client-detail-panel.md)
- [Audit log (SSAL)](03-audit-log.md)
- [Sera Sync](04-sera-sync.md)
- [Settings — General](05-settings-general.md)
- [Settings — Columns](06-settings-columns.md)

## Decided

- **Theme setting:** shows "Dark" as the only option; no light theme planned.
- **EMAIL and TAN column types:** left as they are (`password` [Secret]) for now.

## Fleet mockup

`fleet/` will hold a redesign of every screen as one clickable HTML page, made in a cloud session with Claude Fable 5.1 (work package W0-D; prompt in [overhaul/fleet-redesign-cloud.md](overhaul/fleet-redesign-cloud.md)). Done 2026-10-10: [fleet/index.html](fleet/index.html), [fleet/README.md](fleet/README.md), and [fleet/BUILD-PLAN.md](fleet/BUILD-PLAN.md), the kit-first build order the tracker now follows.

## Build plan

See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the work packages, rollout order, rules and checks. The tracker, dispatcher, runner prompts and launchers are in [overhaul/](overhaul/).
