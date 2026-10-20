# OniHarness for Python

Run **Oxygen Not Included mod tests** from Python and read paused game
observations through a local OniMcp server. Developed with **Python 3.15 and uv**;
the Python API supports Python 3.10+. This is an early 0.1.0 preview.

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
from oniharness import Harness

harness = Harness.from_checkout(
    "./OniHarness", build=True,
    game_path="/path/to/OxygenNotIncluded",
)
print(harness.doctor()["adapter"])

report = harness.run_scenario({
    "name": "Sealed oxygen conserves mass",
    "width": 16, "height": 16, "steps": 50,
    "fill": {"element": "Oxygen", "mass": 1, "temperature": 300},
    "assertions": [{"kind": "massConserved", "absoluteTolerance": 0.01}],
}, repeat=3, report_path="results/oxygen.json")

print(report.passed)
for test in report.tests:
    print(test["status"], test["evidence"], test["fingerprint"])
```

If `oniharness` is already installed on PATH, use `Harness(game_path=...)`.
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
import os
from oniharness import Game, McpClient

client = McpClient(token=os.environ.get("ONIHARNESS_MCP_TOKEN"))
print([tool["name"] for tool in client.list_tools()])
observation = Game(client).observe()  # pauses, verifies pause, reads near camera
print(observation["map"]["text"])
print(observation["dupes"])
```

The game remains paused. `observe(bounds=(x1, y1, x2, y2))` selects inclusive
coordinates, up to 1600 cells. No game launch, resume or camera movement occurs.
JSON, SSE and TOON responses are decoded; tool errors raise `McpError`.
Connections accept HTTP loopback addresses only, with no proxies or redirects.

`client.call_tool(name, arguments)` exposes the installed tool catalog directly.
This low-level API can write to the game and does **not** add pause protection;
use the usual pause → observe → plan → act → short run → pause → verify loop.

More examples and development instructions:
[Python guide](https://github.com/LIghtJUNction/OniHarness/blob/main/docs/python.md).

Original framework code uses **AUL 1.0-draft.1**, a custom license which is not
OSI-approved. Dependencies and ONI retain their own terms. See
[LICENSE](https://github.com/LIghtJUNction/OniHarness/blob/main/LICENSE) and
[LICENSING.md](https://github.com/LIghtJUNction/OniHarness/blob/main/LICENSING.md).
Unofficial community project; not affiliated with Klei or Unity.
