"""Pause, verify and print the real text map; leaves the game paused."""

import argparse
import os

from oniharness import Game, McpClient


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8788/mcp/")
    parser.add_argument("--bounds", nargs=4, type=int, metavar=("X1", "Y1", "X2", "Y2"))
    args = parser.parse_args()
    client = McpClient(args.url, token=os.environ.get("ONIHARNESS_MCP_TOKEN"))
    observation = Game(client).observe(
        bounds=tuple(args.bounds) if args.bounds else None
    )
    print(observation["map"]["text"])
    print(observation["dupes"])


if __name__ == "__main__":
    main()
