# AgentBI v0.2 Delivery Scope

This document defines the Git delivery view for v0.2. It does not delete,
move, overwrite, or reset any local runtime data.

## Include in the delivery review

- AgentBI source code and governed Core modules.
- Python tests, acceptance harnesses, and formal runtime smoke tooling.
- Dockerfile, Compose/deployment configuration, and reproducible examples.
- Published-ontology definitions, contracts, persistence code, and RuntimeBinding
  implementation (without live database contents or credentials).
- Architecture, planning, acceptance, handoff, and checkpoint documentation.
- Superset adapter source and frontend source/package metadata as reviewed
  product source; generated build output is excluded.

## Exclude from delivery

- Local PostgreSQL/AgentBI databases, Docker volumes, recovery copies, and live
  snapshots or database dumps created for local runtime use.
- Secrets, tokens, passwords, `.env` runtime files, and machine-local config.
- `node_modules/`, local `dist/`, build caches, test caches, and runtime logs.
- `.DS_Store`, temporary `phase63a6-*.jar` files, `*.mv.db`, and other local
  recovery/runtime artifacts.
- Any local container export or database volume archive.

The ignore rules are delivery hygiene only; excluded files remain on disk.
No `git add`, commit, push, delete, or volume operation is part of this scope
cleanup.

## npm audit note

The Node 22/npm 10 validation reported six moderate vulnerabilities in the
`webpack-dev-server` development dependency chain (`express`, `body-parser`,
`qs`, `sockjs`, and `uuid`). They are not part of the production webpack
bundle. Non-breaking fixes are indicated for the `qs`/Express chain; fixing the
`sockjs`/`uuid` findings requires evaluating a `webpack-dev-server` 6 major
upgrade. No audit fix or dependency upgrade is performed for v0.2 delivery.
