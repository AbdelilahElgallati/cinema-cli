from dataclasses import dataclass, field
from typing import List


@dataclass
class AppState:
    """Single source of truth for top-level application runtime state."""

    argv: List[str] = field(default_factory=list)
    setup_requested: bool = False
    needs_backend: bool = False
    backend_url: str = ""
