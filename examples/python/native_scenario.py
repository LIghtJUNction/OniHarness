"""Run an isolated native fixture; never starts ONI or Steam."""

import argparse
from pathlib import Path

from oniharness import Harness


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkout",
        type=Path,
        help="OniHarness source checkout (otherwise use CLI on PATH)",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Explicitly build the source backend with .NET SDK 10",
    )
    parser.add_argument("--game-path", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=Path("results/oxygen.json"))
    args = parser.parse_args()
    if args.build and args.checkout is None:
        parser.error("--build requires --checkout")
    harness = (
        Harness.from_checkout(args.checkout, build=args.build, game_path=args.game_path)
        if args.checkout
        else Harness(game_path=args.game_path)
    )
    print("Adapter:", harness.doctor()["adapter"])
    report = harness.run_scenario(
        {
            "name": "Sealed oxygen conserves mass",
            "width": 16,
            "height": 16,
            "steps": 50,
            "fill": {"element": "Oxygen", "mass": 1, "temperature": 300},
            "assertions": [{"kind": "massConserved", "absoluteTolerance": 0.01}],
        },
        repeat=3,
        report_path=args.report,
    )
    for test in report.tests:
        print(test["status"], test["evidence"], test["fingerprint"])
    print("Report:", args.report.resolve())


if __name__ == "__main__":
    main()
