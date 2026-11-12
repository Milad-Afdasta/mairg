# Release-note generator

This independently authored second application converts CSV change entries into
grouped Markdown using MAIRG graphs only. It was built from `mairg new`, the public
README/tutorial/specification and `describe`, without reading or editing runtime
implementation. It elaborates the independent-author/file scenario of approved
model MAIRG-M4; it introduces no runtime contract.

```sh
.venv/bin/mairg check examples/changelog
.venv/bin/mairg test examples/changelog
mkdir -p /tmp/release-state/files
cat > /tmp/release-state/files/changes.csv <<'CSV'
category,title,issue
added,"Ship café, 日本語 support",42
fixed,"Keep ""quoted"" words",0012
CSV
.venv/bin/mairg run examples/changelog generate \
  --args '["changes.csv","release.md","1.2.0"]' \
  --state-dir /tmp/release-state
cat /tmp/release-state/files/release.md
.venv/bin/mairg build examples/changelog --output /tmp/release-bundle
cd /tmp
python3 -I /tmp/release-bundle/run.py generate \
  --args '["changes.csv","release.md","1.2.0"]' \
  --state-dir /tmp/release-state
```

`generate(input_filename, output_filename, version)` returns the exact written
Markdown. Filenames are confined to `STATE_DIR/files`; source and bundle files
are immutable inputs rather than writable application state.

The graph owns all application policy:

- CSV must contain at least one entry. Headers must be exactly `category`,
  `title`, `issue` (header order may vary). The generic CSV host parses Unicode,
  commas, doubled quotes and multiline quoted fields.
- Categories are trimmed and lowercased, then must be `added`, `changed`, or
  `fixed`. Section order is always Added, Changed, Fixed. Empty sections say
  `- None.`.
- Titles replace CR/LF/tab with spaces, trim outer whitespace and reject blank
  content. Markdown punctuation is escaped when rendering titles and versions.
- Issues are trimmed; blank means no reference. Otherwise the decimal string
  must parse to a positive integer, rendered canonically (`0012` becomes `12`).
- Versions are trimmed, nonblank, single-line and at most 64 characters.
- Duplicates are deliberately retained: every input row represents a release
  entry. Input order is stable within each category; rows are never silently
  deduplicated or sorted by their issue number.

All rows and the version are validated before rendering and one atomic output
write. An invalid later row rejects the entire invocation and preserves existing
output bytes. The file host owns temp-file creation, flush/fsync and replacement;
the graph orders it after complete validation. A post-replacement durability
failure has the runtime's documented unknown-outcome semantics; no retry is
performed. Concurrent invocations are independent atomic replacements, not a
merge or lock protocol.

Reusable graph functions implement category/title/issue/version validation,
Markdown escaping, row normalization, filtering, line and section rendering.
`../generate_changelog.py` only constructs JSON source and fixtures; it is never
imported or executed by the application or its bundle. To customize behavior,
edit `program.json` and rebuild. The acceptance test changes a heading constant
and proves the rebuilt standalone artifact changes behavior without runtime edits.

Run the complete external-interface proof with:

```sh
.venv/bin/python -m unittest discover -s tests -p test_independent_application.py -v
```

It asserts actual file bytes, malformed later-row preservation, Unicode/quoting,
duplicates/order, Markdown escaping, empty sections and validation. It builds a
directory, creates a clean interpreter without pip or MAIRG, confirms MAIRG is
unavailable, clears `PYTHONPATH`, and executes that artifact with `-I` outside the
checkout. It then edits only graph JSON, rebuilds and verifies customized output.
