# antibody

Community failure-mode package manager, dual-assertion Antigen/Antibody validator, and
trace-to-vaccine distiller for coding agents.

```
antibody search [query] [--lang L]
antibody pull core/python-safety [--patch-agents-md] [--registry URL]
antibody remove core/python-safety
antibody sync | antibody verify
antibody test [pack_dir] | antibody lint <pack_dir>
antibody distill --from-tape T | --from-flight-db DB | --from-diff P  --out D [--step N] [--llm --model M]
```

Exit codes: 0 ok, 2 bad args / not found, 11 lint gate failure, 12 hash mismatch / drift,
14 distill failed. Pulling a pack never executes pack Python code.

## How it works, and why it's built this way

antibody is a package manager for coding-agent failure modes. Each vaccine pairs an antigen, a fixture that reproduces the failure, with an antibody, a declarative flywire rule that blocks it.

- Every rule has to fire on its Positive Antigen and stay silent on its Negative Control. That rules out vaccines that never trigger as well as ones that block everything. Lint also enforces a latency budget: median under 5 ms, p99 under 15 ms.
- `pull` never executes code. Installed rules are only TOML (tree-sitter queries and regexes) plus Markdown. Pack Python evals run only when you call `antibody test`.
- Each pack has a canonical SHA-256 tree hash: sorted POSIX paths, text files normalized to LF, NUL-separated. The hash is the same on Linux, macOS and Windows and is pinned in `antibody.lock`. Changing one byte makes `verify` exit 12.
- Installs are atomic: packs are staged in `.flywire/.tmp`, hash-checked, then moved into place with `os.replace`. `envelope.toml` is edited with tomlkit so comments survive. `AGENTS.md` blocks are fenced with markers, so re-running gives a byte-identical file and `remove` restores the original bytes.
- `distill` turns a failed agent-rr `.tape` into a draft pack with fixtures sliced from the enclosing AST node, and runs the same 5 lint gates on it.
- The registry is a static Git repository or a local directory with an offline cache. 18 verified starter vaccines ship in the wheel.

Cross-package file formats are documented in `CONTRACTS.md`.
