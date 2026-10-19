# Capabilities and evidence

The table below describes the original game-free/native v0.1.0 harness.
The source checkout also provides an experimental [real-game workflow](runtime.md)
with explicit Steam launch, default no-window/Null-device headless mode, an
optional graphical rendering mode and text spectator.
That mode uses the actual game/Unity runtime; startup is not full compatibility
evidence. Runtime observations and resource measurements are separate from the
fixture results below. CLI/Core v0.1.0 package behavior is unchanged.

| Capability | v0.1.0 status | Evidence established |
| --- | --- | --- |
| Framework and worker lifecycle | Supported; game-free CI | Assertions, async failure, timeout, abrupt exit, output bounds, discovery isolation |
| Native gas/liquid/solid simulation | Verified Linux x64, build 744825 | Small real-library fixture scenarios |
| Cell replacement / energy input | Supported | C# example tests with the installed native library |
| Custom material parameters | Supported with required special elements | Real solver with explicit fixture inputs |
| Repeatability | Compared per repeated JSON scenario | Exact final element/mass/temperature fingerprint for that input and binary |
| Selected installed C# methods | Verified example: Sim.GetHandleIndex | Actual method execution in .NET 10, without Unity internal calls |
| Static method contract | Type/name/parameter-count metadata | Presence only; no parameter-type or patch execution guarantee |
| Harmony patch execution | Not supported | No runtime claim |
| Prefabs, building lifecycle, dupes/AI, chores | Not supported | No runtime claim |
| Full save loading / colony gameplay | Not supported | No runtime claim |
| UI, graphics, sound, Unity physics | Not supported | No runtime claim |
| Phase transitions, disease, radiation | Disabled in initial fixture adapter | No runtime claim |
| Windows/macOS native adapters | Not verified / unsupported | No runtime claim |

Accepted adapter: `oni-744825-linux-x64-v1`.

- `libSimDLL.so`: `85111cd0b1fc14c79c785581d6c055280b9d48ba705776f3d14eb0a7b239c12d`
- `Assembly-CSharp.dll`: `6db652990ad1038e86b3493ed43004e12bfd61e62a4be60c787a9a9b9ac52ee1`

These fingerprints describe one verified local installation. They are not a claim
that build 744825 is the current release, and they do not prove a different file
with the same version label has the same protocol. No override executes unknown
native binaries.

Native simulation uses the game's existing headless/world-generation interface.
The fixture element table intentionally omits production configuration, material
interactions and lifecycle systems. Built-in values are test inputs; use explicit
mod parameters for material tests. A passing fixture does not establish full
in-game behavior, mod interoperability or an FPS improvement.

Public references informing the feasibility research:

- [Unity desktop headless mode](https://docs.unity3d.com/2023.1/Documentation/Manual/desktop-headless-mode.html): still executes a Player; this project uses a separate host instead.
- [Independent SimDLL replay report](https://kleiforums.com/klei-bug-tracker/oni/mob-placement-during-worldgen-is-non-deterministic-r52211/): community reproduction of standalone simulation messages and timing-dependent behavior; not an official interface guarantee.

No third-party harness implementation or replay trace is copied into this project.
