# OniHarness contributor instructions

Read `UPSTREAM.toml`, `LICENSING.md`, and `docs/capabilities.md` before changes.
This is the original OniHarness project. Do not open issues/PRs against itself
merely to satisfy AUL feedback duties. Check existing reports before reporting
confirmed external upstream defects; preserve third-party terms and secrets.

- Never launch ONI or Unity Player as a hidden fallback. Never use SteamCMD or publish Workshop content here.
- Use only the user's installed game files for authorized local native tests; no binary mirrors or downloads.
- Never commit game DLLs/assets, decompiled implementation, credentials or saves.
- Unknown native adapters fail explicitly. Do not advance hashes without protocol review and local verification.
- Keep native simulation, .NET host execution, static metadata and actual gameplay evidence distinct.
- Discovery must not execute test assemblies. Preserve timeout, crash and empty-selection failures.
- Run `python3 scripts/verify.py`; add `--game-path PATH` for authorized real-library checks.
- Run `git diff --check` and package verification before publishing. Preserve unrelated working-tree changes.
