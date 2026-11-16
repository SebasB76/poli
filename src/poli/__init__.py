"""Tools for an ESPOL student's aula virtual (Canvas), for any agent: a CLI and an MCP server."""

from poli.core import (CATALOG_BUDGET, PAUSED_KEY, REFUSED_KEY, Aula, CanvasClient, CoreConfig, load_config,
                             token_fingerprint)

__version__ = "0.1.0"
__all__ = ["Aula", "CATALOG_BUDGET", "CanvasClient", "CoreConfig", "PAUSED_KEY", "REFUSED_KEY", "load_config",
           "token_fingerprint"]
