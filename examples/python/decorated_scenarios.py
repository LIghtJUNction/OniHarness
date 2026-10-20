"""A parameterized native test suite, with no ONI/Steam launch."""

import argparse
from pathlib import Path

from oniharness import Cell, Scenario, Suite, expect, parametrize


def make_suite(
    *, checkout: Path, build: bool = False, game_path: Path | None = None
) -> Suite:
    suite = Suite(checkout=checkout, build=build, game_path=game_path)

    @suite.scenario(repeat=3)
    @parametrize(temperature=[280, 300, 320])
    @expect.mass_conserved()
    @expect.cell_temperature(8, 8, min=270, max=330)
    def sealed_oxygen(temperature):
        return Scenario(fill=Cell("Oxygen", temperature=temperature))

    return suite


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkout", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Explicitly build the backend with .NET SDK 10",
    )
    parser.add_argument(
        "--game-path", type=Path, help="Otherwise use ONIHARNESS_GAME_PATH"
    )
    parser.add_argument("--filter", help="Select case names, e.g. temperature=300")
    parser.add_argument("--report", type=Path, default=Path("results/decorated.json"))
    args = parser.parse_args()
    suite = make_suite(
        checkout=args.checkout, build=args.build, game_path=args.game_path
    )
    print(suite.run(filter=args.filter, report_path=args.report))
    print("Report:", args.report.resolve())


if __name__ == "__main__":
    main()
