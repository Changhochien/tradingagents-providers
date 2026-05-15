"""
Test configuration for tradingagents-providers tests.
"""

import sys
from pathlib import Path

# Add the package source to path
src_dir = Path(__file__).parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

# Add the repository root so tests can exercise TradingAgents core hooks.
repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))
