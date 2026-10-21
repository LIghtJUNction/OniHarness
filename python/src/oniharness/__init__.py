"""OniHarness native tests and explicit connections to a running OniMcp server."""

from ._version import __version__
from .harness import CommandError, Harness, HarnessError, TestFailure, TestReport
from .mcp import Game, McpClient, McpError
from .notebook import GameFrame
from .scenario import Cell, Expectation, Region, Scenario, expect
from .suite import DefinitionError, Suite, parametrize

__all__ = [
    "Cell",
    "CommandError",
    "DefinitionError",
    "Expectation",
    "Game",
    "GameFrame",
    "Harness",
    "HarnessError",
    "McpClient",
    "McpError",
    "Region",
    "Scenario",
    "Suite",
    "TestFailure",
    "TestReport",
    "__version__",
    "expect",
    "parametrize",
]
