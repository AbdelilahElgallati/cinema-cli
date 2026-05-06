from dataclasses import dataclass, field


@dataclass
class AppState:
    """Single source of truth for top-level application runtime state."""

    argv: list[str] = field(default_factory=list)
    setup_requested: bool = False
    needs_backend: bool = False
    backend_url: str = ""
