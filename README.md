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
