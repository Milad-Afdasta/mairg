"""Author JSON graphs/fixtures only; application execution never imports this helper."""

import json
from pathlib import Path

ROOT = Path(__file__).parent / "changelog"
FUNCTIONS = []


class Function:
    def __init__(self, params, result):
        self.id = len(FUNCTIONS)
        self.document = {"id": self.id, "params": params, "result": result, "nodes": []}
        FUNCTIONS.append(self.document)
        self.params = [self.node("param", index=i) for i in range(len(params))]

    def node(self, op, *args, **fields):
        node_id = len(self.document["nodes"])
        self.document["nodes"].append(
            {"id": node_id, "op": op, "args": list(args), **fields}
        )
        return node_id

    def const(self, value):
        return self.node("const", value=value)

    def field(self, record, key):
        return self.node("field", record, self.const(key))

    def call(self, function, *args):
        return self.node("call", *args, target=function.id)

    def choose(self, condition, yes, no, *args):
        return self.node("choose", condition, *args, then=yes.id, **{"else": no.id})

    def join(self, *nodes):
        return self.node("text.join", self.const(""), self.node("list", *nodes))

    def done(self, node):
        self.document["return"] = node
        return self


def error(params, code, message):
    f = Function(params, "any")
    return f.done(f.node("fail", f.const(code), f.const(message)))


identity = Function(["str"], "str")
identity.done(identity.params[0])
bad_version = error(
    ["str"],
    "invalid_version",
    "Version must be nonblank, single-line and at most 64 characters",
)
version = Function(["str"], "str")
v = version.node("text.trim", version.params[0])
ok = version.node("gt", version.node("length", v), version.const(0))
ok = version.node(
    "and", ok, version.node("le", version.node("length", v), version.const(64))
)
for character in ("\n", "\r"):
    ok = version.node(
        "and",
        ok,
        version.node("not", version.node("contains", v, version.const(character))),
    )
version.done(version.choose(ok, identity, bad_version, v))

escape = Function(["str"], "str")
text = escape.params[0]
for character in ("\\", "`", "*", "_", "[", "]", "<", ">", "#", "!", "|"):
    text = escape.node(
        "text.replace", text, escape.const(character), escape.const("\\" + character)
    )
escape.done(text)

bad_title = error(["str"], "invalid_title", "Title must contain visible text")
title = Function(["str"], "str")
t = title.params[0]
for character in ("\r", "\n", "\t"):
    t = title.node("text.replace", t, title.const(character), title.const(" "))
t = title.node("text.trim", t)
title.done(
    title.choose(
        title.node("gt", title.node("length", t), title.const(0)),
        identity,
        bad_title,
        t,
    )
)

bad_category = error(
    ["str"], "invalid_category", "Category must be added, changed or fixed"
)
category = Function(["str"], "str")
c = category.node("text.lower", category.node("text.trim", category.params[0]))
category.done(
    category.choose(
        category.node("contains", category.const(["added", "changed", "fixed"]), c),
        identity,
        bad_category,
        c,
    )
)

parse = Function(["str"], "int")
parse.done(parse.node("parse_int", parse.params[0]))
bad_parse = error(
    ["record"], "invalid_issue", "Issue must be empty or a positive decimal integer"
)
bad_integer = error(
    ["int"], "invalid_issue", "Issue must be empty or a positive decimal integer"
)
integer_text = Function(["int"], "str")
integer_text.done(integer_text.node("to_text", integer_text.params[0]))
positive_issue = Function(["record"], "str")
i = positive_issue.field(positive_issue.params[0], "value")
positive_issue.done(
    positive_issue.choose(
        positive_issue.node("gt", i, positive_issue.const(0)),
        integer_text,
        bad_integer,
        i,
    )
)
nonempty_issue = Function(["str"], "str")
attempt = nonempty_issue.node("attempt", nonempty_issue.params[0], target=parse.id)
nonempty_issue.done(
    nonempty_issue.choose(
        nonempty_issue.field(attempt, "ok"), positive_issue, bad_parse, attempt
    )
)
issue = Function(["str"], "str")
i = issue.node("text.trim", issue.params[0])
issue.done(
    issue.choose(issue.node("eq", i, issue.const("")), identity, nonempty_issue, i)
)

normalize_row = Function(["record"], "record")
r = normalize_row.params[0]
values = [
    normalize_row.call(fn, normalize_row.field(r, key))
    for key, fn in (("category", category), ("title", title), ("issue", issue))
]
normalize_row.done(
    normalize_row.node("record", *values, keys=["category", "title", "issue"])
)
bad_columns = error(
    ["record"], "invalid_columns", "CSV headers must be exactly category,title,issue"
)
row = Function(["record"], "record")
r = row.params[0]
row.done(
    row.choose(
        row.node("eq", row.node("keys", r), row.const(["category", "issue", "title"])),
        normalize_row,
        bad_columns,
        r,
    )
)

plain_line = Function(["record"], "str")
plain_line.done(
    plain_line.join(
        plain_line.const("- "),
        plain_line.call(escape, plain_line.field(plain_line.params[0], "title")),
    )
)
issue_line = Function(["record"], "str")
r = issue_line.params[0]
issue_line.done(
    issue_line.join(
        issue_line.call(plain_line, r),
        issue_line.const(" (#"),
        issue_line.field(r, "issue"),
        issue_line.const(")"),
    )
)
line = Function(["record"], "str")
r = line.params[0]
line.done(
    line.choose(
        line.node("eq", line.field(r, "issue"), line.const("")),
        plain_line,
        issue_line,
        r,
    )
)

none = Function(["list"], "str")
none.done(none.const("- None."))
lines = Function(["list"], "str")
lines.done(
    lines.node(
        "text.join",
        lines.const("\n"),
        lines.node("map", lines.params[0], target=line.id),
    )
)
section = Function(["list", "str"], "str")
rows, heading = section.params
body = section.choose(
    section.node("eq", section.node("length", rows), section.const(0)),
    none,
    lines,
    rows,
)
section.done(
    section.join(
        section.const("## "), heading, section.const("\n"), body, section.const("\n\n")
    )
)

selectors = []
for name in ("added", "changed", "fixed"):
    select = Function(["record"], "bool")
    select.done(
        select.node(
            "eq", select.field(select.params[0], "category"), select.const(name)
        )
    )
    selectors.append(select)

render = Function(["list", "str"], "str")
rows, v = render.params
sections = [
    render.call(
        section, render.node("filter", rows, target=select.id), render.const(heading)
    )
    for select, heading in zip(selectors, ("Added", "Changed", "Fixed"))
]
render.done(
    render.join(
        render.const("# Release "),
        render.call(escape, v),
        render.const("\n\n"),
        *sections,
    )
)
bad_empty = error(["list"], "empty_csv", "CSV must contain at least one change")
keep_rows = Function(["list"], "list")
keep_rows.done(keep_rows.params[0])
nonempty_rows = Function(["list"], "list")
rs = nonempty_rows.params[0]
nonempty_rows.done(
    nonempty_rows.choose(
        nonempty_rows.node(
            "gt", nonempty_rows.node("length", rs), nonempty_rows.const(0)
        ),
        keep_rows,
        bad_empty,
        rs,
    )
)

generate = Function(["str", "str", "str"], "str")
input_path, output_path, raw_version = generate.params
rows = generate.node("host", input_path, binding="read_csv")
rows = generate.call(nonempty_rows, rows)
rows = generate.node("map", rows, target=row.id)
markdown = generate.call(render, rows, generate.call(version, raw_version))
write = generate.node("host", output_path, markdown, binding="write_text")
generate.done(generate.node("call", markdown, target=identity.id, after=[write]))

program = {
    "format": "mairg-app-1",
    "functions": FUNCTIONS,
    "exports": {"generate": generate.id},
}
manifest = {
    "format": "mairg-project-1",
    "name": "changelog",
    "program": "program.json",
    "bindings": {"read_csv": "file.read_csv", "write_text": "file.write_text"},
    "database": None,
    "assets": [],
    "routes": [],
    "startup": None,
    "tests": ["tests.json"],
}
CSV = 'category,title,issue\n Fixed ,"Fix café, quoting",0012\nADDED,"Ship 日本語 support",42\nchanged,"Keep ""quoted"" words",\nfixed,"Fix café, quoting",12\n'
MARKDOWN = '# Release 1.2.0\n\n## Added\n- Ship 日本語 support (#42)\n\n## Changed\n- Keep "quoted" words\n\n## Fixed\n- Fix café, quoting (#12)\n- Fix café, quoting (#12)\n\n'
fixture = {
    "files": {
        "changes.csv": CSV,
        "bad-category.csv": "category,title,issue\nadded,Good,1\nunknown,Bad,2\n",
        "bad-title.csv": "category,title,issue\nfixed,   ,1\n",
        "bad-issue.csv": "category,title,issue\nfixed,Bad,abc\n",
    },
    "cases": [
        {
            "export": "generate",
            "args": ["changes.csv", "release.md", " 1.2.0 "],
            "expect": MARKDOWN,
        },
        {
            "export": "generate",
            "args": ["bad-category.csv", "release.md", "1.2.0"],
            "error": "invalid_category",
        },
        {
            "export": "generate",
            "args": ["bad-title.csv", "release.md", "1.2.0"],
            "error": "invalid_title",
        },
        {
            "export": "generate",
            "args": ["bad-issue.csv", "release.md", "1.2.0"],
            "error": "invalid_issue",
        },
    ],
}

if __name__ == "__main__":
    for path, document in (
        ("program.json", program),
        ("mairg.json", manifest),
        ("tests.json", fixture),
    ):
        (ROOT / path).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
