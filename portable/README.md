# OniHarness Linux x64 complete private handoff

This user-directed private package includes the complete installed Linux ONI
744825 game directory, CycleTrim's experimental layout build, OniMcp, the
headless companion, a development test save copy, source checkouts, an official
.NET 10.0.112 SDK, standalone CPython 3.12, an offline NuGet feed, and measurements.
Game files are supplied privately at the owner's request. They are not part of
the open-source projects' licenses and must not be committed or publicly released.
Steam account files, login caches, tokens, personal cloud-save folders and raw
personal logs are excluded. The fixture is a three-duplicant development save
with pre-existing test objects; it is not a survival/late-game benchmark.

## First run

Use a Linux x86-64 GNU/glibc machine. Extract with `unzip`; run from a path without
spaces. Python and .NET are included. A compatible Linux system, Steam client,
your own Steam login, ONI/Spaced Out entitlement, and access to that Steam session
are still required. No DRM bypass or direct game-executable fallback exists.
The official `steam_latest.deb` is included for Debian/Ubuntu; other distributions
should install Steam through their supported package manager. Steam's initial
setup/runtime download and sign-in can require network/display access. Desktop-free
Steam bootstrap is not verified. The ONI test itself runs with no game window or GPU.

```bash
bash run.sh verify
bash run.sh doctor
bash run.sh build
```

`verify` checks SHA-256 and restores executable modes. `build` uses the supplied
offline feed and game references, runs host/Harness checks, and stages rebuilt
mod DLLs for `install`. Source repositories are local snapshots; original upstream
revisions and download provenance are recorded in `PROVENANCE.json`.

Import the included game into an **existing registered Steam library** if ONI
is not installed there. Close Steam before import. This creates a game-directory
symlink and a sanitized app manifest; it refuses to overwrite an existing game
or manifest. An existing installation must match the supplied core hashes.

```bash
bash run.sh import-game --steamapps /path/to/Steam/steamapps
# Start Steam normally and sign in with your own licensed account.
bash run.sh install
bash run.sh launch --steamapps /path/to/Steam/steamapps
bash run.sh load
bash run.sh measure --cpu 0
bash run.sh stop
```

The default Steam library is detected when `--steamapps` is omitted. Do not move
the extracted game while a Steam library points to it. Steam can update game
files; a changed fingerprint needs new acceptance, not relaxed hash checks.

`install` refuses a running game, preserves other mod entries, backs up replaced
lab files/manifest under `~/Backups/OniHarness/portable/`, and installs only the
three lab mods plus the disposable save copy. For a minimal three-mod baseline,
disable other mods in your test profile. Existing unrelated mods are not disabled
automatically. ONI can disable a changed mod during native reinstall: stop the
owned test game, rerun `install`, launch a fresh session and verify the layout log.
Do not treat an unloaded candidate as a successful optimization.

## Experiment and iteration

```bash
bash run.sh micro --cpu 0
```

The micro test alternates five runs per layout for each workload size, after
warmup, and uses the same production generic candidate-buffer helper. Candidate
identities/checksums must agree. Hardware events count only the explicitly timed
main-thread loop; no compilation/JSON reporting is inside that counter window.
This is .NET 10 synthetic evidence, separate from the game's Mono runtime.

`measure` alternates baseline/compact in one real paused headless game, performs
an equal warmup before each sample and five guarded three-second steps per side.
It records process CPU/native time, main-thread generic cache events, paging,
actual layout call counts and final pause. Only the game main thread is pinned
to `--cpu`; worker affinity is unchanged. Pick a suitable available CPU, especially
on hybrid P/E processors. The save continues evolving across samples.

Permission-denied/unsupported PMUs fail the cache experiment explicitly. On such
machines use `sources/OniHarness/scripts/measure_steps.py --output SESSION --label NAME`
for CPU/native-clock tests, and the C# helper tests for correctness; no cache
result is available. Do not weaken the host's perf security automatically.

Raw repeated measurements and limitations are in `evidence/`. In this first
experiment compact indexing improved the synthetic helper; the native run did
not establish a decision-grade whole-game speedup. Generic cache events do not
represent a universal L1/L2/L3 hit rate. A high miss/reference ratio alone is not
a diagnosis. Compare total work and CPU/native time, with paging and variation.

Modify `sources/OniMods/mods/CycleTrim/`, run `build`, stop the owned game, install,
launch/load and repeat. Ordinary launches keep the established baseline layout;
the compact layout is selected only by the explicit lab control argument. Keep
simulation eligibility, ranking, tie order, invalidation and game-owned stop
deadlines intact. No debug spawning or cheat resources. Keep the game paused
for inspection/actions and use bounded steps. Use `AI_HANDOFF.md` as the next task.
