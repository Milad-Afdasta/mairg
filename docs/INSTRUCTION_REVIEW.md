# Instruction improvement

Classification: execution miss. The first target optimized for bounded numeric delivery rather than proving the user's application-building goal. Existing correctness/completion and owner-first/falsification rules already required validating the actual goal. Execution diverged by using kernel benchmarks as product evidence.

Owning standing instructions: “trace producer, source-of-truth owner, persistence, consumer, transformations, side effects, and tests in both directions” and “Include at least one concrete disconfirming case that would expose the proposed owner or invariant as wrong.” These already require the goal and boundary checks below; no reusable process gap was established.

Enforcement: practical POC acceptance requires a real persistent API/CLI, independent second app without runtime edits, outside-checkout distribution, and complete-application comparison. Review then caught resource-domain aliasing and HTTP framing ownership; both corrected in MAIRG-M4 before product edits. Existing source-of-truth/boundary/side-effect instructions already cover these reasoning steps. No instruction edit was warranted.

The independent semantics tester found that describe() returned shared internal signature/effect lists. This is an execution miss: existing canonical-owner/dataflow invariants and M4 explicitly required detached public values. The shallow construction overlooked nested list ownership. Root now snapshots the whole public view; a regression mutates it and proves original invocation behavior remains intact. No instruction edit warranted.

Distribution inspection found runtime notices missing while the dependency notice was copied. Existing artifact-content/source-owner validation already applied; this is an execution miss. The builder now carries both notices from their owning installed distributions, checks actual artifact contents, and the clean-wheel workflow passes. No instruction edit warranted.

The first practical CI run found a test helper assumed a local virtualenv path instead of the executing interpreter. This is an execution miss: existing source-of-truth and runtime/dependency identity requirements already cover test-process ownership. The helper now uses sys.executable; a repository scan found no other runtime/test hardcoding of that path, all eight affected tests pass locally, and the Linux/macOS Python3.11/3.14 matrix verifies portability. No instruction edit was warranted.
