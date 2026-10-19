# Continue the cache-locality experiment

Goal: improve CycleTrim's real ONI work per native simulation second without
changing game results, and use OniHarness to prove each iteration in true headless
mode. The receiver is Linux x64; the game, own mods, toolchains and fixture are
included. Read README.md, PROVENANCE.json and evidence before making changes.

Start with `bash run.sh verify`, `doctor` and `build`. Use Steam only to launch
the licensed game, then install/launch/load as documented. There is no account
or token in the archive; use your own authorized Steam session. Preserve other
applications, personal saves and work. Do not publish included proprietary files.

Experiment: `FetchCandidateBuffer<TKey,TValue>` compares full dictionary values
against a key-to-index dictionary plus the native destination list. The dictionary
does not cache state across game ticks. Winner identities, first-wins ties,
collisions, reuse and empty input are tested. `FetchLayoutExperiment` is opt-in,
acknowledges nonce/PID and counts actual calls so the measurement cannot silently
test another layout. Ordinary launches default to baseline. Do not promote the
candidate merely because one synthetic test wins.

Measure at least five alternating pairs with identical warmup/configuration.
Separate helper/.NET data from native Mono, whole-process CPU from main-thread
PMU events, maximum throughput from a 1x clock, and RSS from memory saved. Record
failures and check actual positive native progress and final pause. Paging,
background load, changed save state and instrumentation can dominate small gains.
Use a larger representative save only with authorization and privacy review.

No L1/L2-specific, bandwidth, stall-cycle, late-game or universal speedup claim is
established. Hardware counters may be unavailable on another machine; never invent
zeros or label timing-only data as cache measurements. Linux generic hardware
events are architecture dependent. The measurement adapter uses perf_event_open
with grouped user-space events and reports scheduling coverage. Enabled/running
times are window deltas because RESET does not reset those cumulative clocks.

Source snapshots preserve AUL/third-party notices and public upstream identities.
Original revision IDs are in PROVENANCE.json. New downstreams must follow the
license/upstream contribution rules; do not elect independence merely by copying
UPSTREAM.toml. Keep improvements reviewable and submit evidence-supported changes
upstream when authorized. Source changes from this handoff remain experimental.

AgentGC tracking could not start on the sender because its existing shared state
was over budget. No complete writer history is claimed. Backups and failed traces
were retained locally; unrelated configuration edits were preserved.
