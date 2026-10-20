# OniHarness for Python

Run **Oxygen Not Included mod tests** from Python and read paused game
observations through a local OniMcp server. Developed with **Python 3.15 and uv**;
the Python API supports Python 3.10+. This is an early 0.2.0 preview.

```sh
uv pip install oniharness
```

For a uv project, use `uv add oniharness` instead.

## Native simulation tests

Native tests require the separate OniHarness CLI, .NET 10, and your own local
ONI installation. The Python wheel contains no game binaries and no .NET
backend. To build the backend from the official source checkout:

```sh
git clone https://github.com/LIghtJUNction/OniHarness.git
```

```python
from oniharness import Cell, Scenario, Suite, expect, parametrize

suite = Suite(
    checkout="./OniHarness", build=True,
    game_path="/path/to/OxygenNotIncluded",
)

@suite.scenario(repeat=3)
@parametrize(temperature=[280, 300, 320])
@expect.mass_conserved()
def oxygen(temperature):
    return Scenario(fill=Cell("Oxygen", temperature=temperature))

print(suite.run(report_path="results/oxygen.json"))  # PASS: 9/9 tests passed
```

Decorators register definitions; importing the test file never builds a backend
or runs physics. `suite.run()` prepares every selected definition before any
backend execution, then uses isolated CLI workers. Each parameter combination
has a readable case name. `suite.run(filter="temperature=300")` selects one.
Decorator order is flexible. Multiple/stacked parameter axes form a Cartesian
product. Empty selections and duplicate case names are explicit errors.

Assertions work both as decorators and values:
`Scenario().expect(expect.mass_conserved())`. `Cell`, `Region` and `Scenario`
describe fixture inputs; the backend performs the physical/schema validation.
`expect` also provides `total_mass`, `cell_mass` and `cell_temperature`, with
`min` and/or `max` bounds. `report.save(path)` atomically saves the JSON report.

The original `Harness` API remains available. If `oniharness` is already
installed on PATH, use `Harness(game_path=...)` or `Suite(game_path=...)`.
Use `test_scenarios(path)` for JSON files/directories and
`test_assembly(path, filter="...")` for trusted C# test assemblies.

Failures, unsupported binaries, crashes and worker timeouts raise `TestFailure`;
its `.report` retains the original results. `check=False` returns those failed
results for inspection. Setup failures raise `CommandError`; `.stdout`,
`.stderr` and `.returncode` retain the diagnostics. Empty selections fail.

The verified native adapter is **Linux x64 / ONI build 744825**, with exact
installed-file hashes. Other binaries fail explicitly. Fixture tests never
launch Steam, ONI or Unity. They do not establish full colony/mod compatibility.
Temperature is Kelvin, mass is kilograms and energy is kilojoules.

## Observe a running game

This only needs Python and an already running game with OniMcp enabled.
Installation and explicit Steam launch are described in the
[runtime guide](https://github.com/LIghtJUNction/OniHarness/blob/main/docs/runtime.md).

```python
from oniharness import Game

game = Game()  # connects locally; reads ONIHARNESS_MCP_TOKEN when set
observation = game.observe()  # pauses, verifies pause, reads near camera
print(observation["map"]["text"])
print(observation["dupes"])
```

The game remains paused. `observe(bounds=(x1, y1, x2, y2))` selects inclusive
coordinates, up to 1600 cells. No game launch, resume or camera movement occurs.
JSON, SSE and TOON responses are decoded; tool errors raise `McpError`.
Connections accept HTTP loopback addresses only, with no proxies or redirects.

For a composed synchronous operation, `@game.paused` verifies pause before the
body and again in `finally`, including when the body raises. It preserves the
function's name, signature and return value. Async/generator work is rejected
so it cannot escape the pause scope. This is not a bounded stepping API.

`game.client.call_tool(name, arguments)` exposes the installed tool catalog directly.
This low-level API can write to the game and does **not** add pause protection;
use the usual pause → observe → plan → act → short run → pause → verify loop.

More examples and development instructions:
[Python guide](https://github.com/LIghtJUNction/OniHarness/blob/main/docs/python.md).

Original framework code uses **AUL 1.0-draft.1**, a custom license which is not
OSI-approved. Dependencies and ONI retain their own terms. See
[LICENSE](https://github.com/LIghtJUNction/OniHarness/blob/main/LICENSE) and
[LICENSING.md](https://github.com/LIghtJUNction/OniHarness/blob/main/LICENSING.md).
Unofficial community project; not affiliated with Klei or Unity.
