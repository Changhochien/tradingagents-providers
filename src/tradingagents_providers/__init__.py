"""
tradingagents-providers: LLM provider plugin package for TradingAgents

This package provides LLM provider profiles for TradingAgents. For current
upstream TradingAgents releases that do not expose extension hooks, the package
auto-installs a small compatibility bootstrap at Python startup.

Usage:
    pip install tradingagents-providers
    tradingagents providers list

The package also registers itself via the 'tradingagents.model_providers' entry
point for future TradingAgents releases with official plugin discovery.
"""

__version__ = "1.0.0"
