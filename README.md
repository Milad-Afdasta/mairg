# MAIRG — application-graph proof of concept

[GitHub](https://github.com/Milad-Afdasta/mairg) · [MIT license](LICENSE)

MAIRG tests machine-authored programming for useful applications. Programs describe reusable functions and dependency graphs; the compiler chooses evaluation order. Internal IDs and declaration order carry no business meaning. Classes, handwritten loops, and manual schedules are not required.

This is an **experimental practical POC**, not a production platform. Its real applications are a persistent inventory/reservation API with a CLI and an independently authored release-note generator. The second author used public docs and tools without reading or editing the runtime.

## Try a new project

Python 3.11+ is required. From a checkout:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/mairg new /tmp/mairg-hello
.venv/bin/mairg check /tmp/mairg-hello
.venv/bin/mairg run /tmp/mairg-hello main --args '["  hello  "]'
.venv/bin/mairg test /tmp/mairg-hello
.venv/bin/mairg describe /tmp/mairg-hello
```

The starter is editable graph source, not a special runtime operation. `describe` exposes the operation/binding catalog, exports, and inferred effects for agents. Failures report structured codes and function/node locations.

## Run a useful application

```sh
.venv/bin/mairg test examples/inventory
.venv/bin/mairg run examples/inventory init --args '[]' --state-dir /tmp/inventory-state
.venv/bin/mairg run examples/inventory create_item --args '["Coffee",10]' --state-dir /tmp/inventory-state
.venv/bin/mairg serve examples/inventory --host 127.0.0.1 --port 8000 --state-dir /tmp/inventory-state
```

The graph owns CRUD, string/quantity validation, schema checks, and reservation decisions. A reservation atomically decrements stock and inserts its record. SQLite and Waitress provide ordinary storage/HTTP infrastructure; there is no inventory operation in the runtime.

See the [inventory guide](examples/inventory/README.md) and [tutorial](docs/TUTORIAL.md).

The [release-note generator](examples/changelog/README.md) reads CSV, validates and groups changes, escapes Markdown, and atomically writes a changelog. Invalid later rows preserve an existing output. Its tests build and run the result with an interpreter that has no MAIRG installation.

## Build and move it

```sh
.venv/bin/mairg build examples/inventory --output /tmp/inventory-bundle
cd /tmp
python3 /tmp/inventory-bundle/run.py list_items --args '[]' --state-dir /tmp/inventory-state
python3 /tmp/inventory-bundle/run.py serve --host 127.0.0.1 --port 8000 --state-dir /tmp/inventory-state
```

The directory includes generated Python, the runtime, Waitress, immutable assets, and integrity metadata. It runs outside the checkout without installing MAIRG or a C compiler. Python remains the execution host. Mutable state is external to the bundle: files under `STATE_DIR/files`, SQLite under `STATE_DIR/db`.

## Language essentials

- Strings, exact integers, finite floats, booleans, null, records, and lists.
- Reusable functions, lazy branches, explicit error recovery, recursion, pure map/filter, and ordered folds.
- Unordered function/node declarations; explicit data/effect dependencies; shared producers evaluated once.
- Parameterized SQLite queries and DB-only transactions with actual commit/rollback.
- CSV/text input and atomic file output; immutable application assets.
- Whole-project checks, isolated fixtures, source-revision patches, and generated artifacts.

A small helper illustrates the source shape:

```json
{"format":"mairg-app-1","functions":[{"id":17,"params":["str"],"result":"str","nodes":[{"id":9,"op":"text.lower","args":[4]},{"id":4,"op":"text.trim","args":[2]},{"id":2,"op":"param","args":[],"index":0}],"return":9}],"exports":{"normalize":17}}
```

Forward references are valid. External exports, record fields, routes, and resource paths remain meaningful interfaces. Exact syntax, operations, effects, resource limits, and failures are defined by the sole [specification](docs/SPEC.md).

## Evidence and scope

[Validation](docs/VALIDATION.md) records practical app, persistence, independent-author, packaging, and CI results. [Comparison](docs/COMPARISON.md) records complete-application measurements and their limits. The earlier numeric-only experiment does not validate this POC and is preserved only in Git history.

AI generation success, token efficiency, and repair/maintenance improvements remain hypotheses until controlled trials. JSON is a transport choice, not claimed optimal tokenization. This POC defers multi-module imports, arbitrary third-party adapters, outgoing HTTP, broad deployment tooling, and native optimization. It does not claim sandboxing, production readiness, or automatic rollback across resources.

## Development

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/mypy mairg
.venv/bin/ruff check mairg tests examples comparison
```

The project follows owner-first specifications and independent review/testing. [Model closure](docs/MODEL_CLOSURE.md) and [review evidence](docs/reviews/specification.md) record the corrected practical scope. Compiler/dataflow ideas are established prior art; MAIRG is an experiment in an integrated machine-authoring workflow.
