"""OniHarness native tests and explicit connections to a running OniMcp server."""

from .harness import CommandError, Harness, HarnessError, TestFailure, TestReport
from .mcp import Game, McpClient, McpError

__version__ = "0.1.0"
__all__ = [
    "CommandError",
    "Game",
    "Harness",
    "HarnessError",
    "McpClient",
    "McpError",
    "TestFailure",
    "TestReport",
    "__version__",
]
