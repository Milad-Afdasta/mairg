# Build your own MAIRG POC application

This tutorial uses only public source contracts and tools. The source of truth for syntax is SPEC.md; `mairg describe PROJECT` exposes executable catalog metadata.

## Create, edit, run

Create a starter with `mairg new my-app`. It writes `mairg.json`, `program.json`, and `tests.json`. The default CLI export `main` trims one string: `mairg run my-app main --args '["  hello  "]'` returns `"hello"`.

A function has an opaque ID, argument type atoms, a result type, unordered nodes, and a return node ID. Nodes refer to producer IDs, including forward references. A `param` node selects one argument; `const` holds JSON; `record` pairs its `keys` with argument-node values. IDs organize references, not execution sequence.

Add another function and export it by name. Use `call` with a target function ID, or `choose` with strict-bool condition and `then`/`else` function IDs. Only the chosen function runs. `map` and `filter` call pure unary functions; `fold` calls a two-argument function with accumulator and item. Function type atoms are runtime-checked; this POC does not promise complete inference over arbitrary JSON.

Check after changes with `mairg check my-app`. Inspect the generated plan/catalog through `describe`. Errors are structured JSON on stderr with source function/node locations. There is no need to change the runtime to add a function, field, algorithm, route, or SQL query.

## Files and data

Declare file bindings in `mairg.json`, for example `"read":"file.read_csv"` and `"write":"file.write_text"`. Invoke a binding using a `host` node with its `binding` name. Supply `--state-dir /absolute/data-root` when running. A logical path `input.csv` means `/absolute/data-root/files/input.csv`.

Generic file hosts cannot reach `/absolute/data-root/db`, database sidecars, or another root. Normalize/convert CSV values explicitly using graph functions. A malformed CSV row rejects rather than silently disappearing. Compute a complete result before the output host; atomic replacement then preserves an existing file if the operation fails before publication.

Every effect must be reachable from the return node. If its value is unused, add an `after` edge to a returning node. Conflicting file/DB effects need a causal path; declaration order never supplies one. An ordered fold can express sequential state changes without writing a host-language loop.

## Persistence and transactions

The inventory example shows a project-owned schema and migration. `schema.json` is a JSON array of individual SQL statements; `migrations/001.json` produces the same live schema. The graph's explicit `init` export applies it; the runtime has no application tables or automatic schema repair.

`sqlite.query` takes SQL and separate parameter values and is genuinely read-only. `sqlite.execute` returns actual rows and changes, including SQL RETURNING rows. Never concatenate input into SQL. A `transaction` node invokes a DB-only target on one actual SQLite transaction; checked success commits, failure rolls back. Network/file effects and nested transactions are not permitted in that region.

The example's startup export verifies version and actual table SQL before the server becomes ready. Confirmed records survive restart when the same explicit state directory is supplied. An error after a committed transaction cannot undo that commit; use `attempt`/`choose` to report partial outcomes truthfully.

## HTTP

The API starter is `mairg new my-api --template api`. Routes select exported one-argument functions. Input contains method/path/params/query/headers/body. Return a record with status, headers, and JSON body. Application headers cannot override Content-Length, Content-Type, or hop-by-hop framing; the transport owns those fields and UTF-8 byte length.

Use `mairg serve my-api --host 127.0.0.1 --port 8000`. Waitress owns HTTP protocol serving. A configured startup export runs before readiness. Each request gets separate frames, budgets, and connection ownership. This is a POC, not a security/deployment framework.

## Test and package

Each test-fixture file has `files` and ordered `cases`. Fixture files are written under a new isolated state/files root. Cases call an export with args and expect either an exact JSON value or an error code. Unexpected failure stops that fixture; later independent fixtures get new state.

Run `mairg test my-app`. Then `mairg build my-app --output my-dist`. Existing output rejects. Building stages generated program/runtime/Waitress/assets, performs an import smoke, writes integrity metadata, and publishes a complete directory. A failed build leaves no runnable partial output.

Move the directory and run `python my-dist/run.py EXPORT --args JSON --state-dir /absolute/state`. No MAIRG installation or C compiler is needed. State must remain outside the immutable bundle. Changing business logic requires editing source and rebuilding; bundle execution never falls back to a stale source copy.

For machine-edited source, `mairg patch PROJECT PATCHFILE` accepts `{base,program}`, validates the complete replacement against the current source revision, and returns it without mutating files. Stale/invalid changes reject atomically.
