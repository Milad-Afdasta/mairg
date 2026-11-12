# Independent practical POC validation

All three NEW test agents used approved MAIRG-M4. They did not edit production runtime code. Tests use fresh state, real SQLite, assigned Waitress ports/processes, and built artifacts.

| Lens | Result | Direct evidence |
|---|---|---|
| Semantics/effects | PASS, 15 independent tests | All operations/control;32 seeded oracle DAGs;8 effect-order renamings; real SQL once/rollback; shared limits; detached values |
| Persistence/HTTP/distribution | PASS, 8 independent tests | Actual CRUD/Unicode/quotes; simultaneous reservations; raw DB/no oversell; later failure rollback; retained version1 source/bundle restart; framing/disconnect; schema refusal |
| Fresh different application author | PASS, 5 independent tests | Graph-only release-note generator, public docs/CLI only; no runtime reading/edit; real output and bad-later-row preservation; isolated bundle; graph-only customization |

The fresh app is examples/changelog. Its author used `mairg new`, `describe`, `check`, `test`, `run`, and `build`, and ran the artifact with `-I`, cleared PYTHONPATH, and a clean interpreter where MAIRG was absent. Logic includes CSV/string/record/list processing, conditional validation, grouping, Markdown rendering, and one atomic write. No Python business callback was needed.

One implementation defect was found: describe() shared internal parameter/effect lists. Root corrected the complete public view to return a detached JSON snapshot. The independent regression mutates returned metadata and then calls the original signature; it passes. The model did not change.

Builder tests add actual active-handler shutdown, unknown commit/durability fault injection, failed-build cleanup, concurrent publication, and schema/migration/master equivalence. Packaging integration also checks runtime/dependency notices and a tool installed from a clean wheel.

Prior numeric-only test/CI results are not evidence for this POC. Whole-app comparison is recorded separately after useful-app acceptance.
