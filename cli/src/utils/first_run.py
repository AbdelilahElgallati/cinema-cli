"""
first_run.py — Cinema CLI first-run setup wizard
=================================================
Shown automatically on the very first launch (or when invoked with --setup).
Guides the user through:
  1. Checking Python version
  2. Checking / installing required tools (mpv, ffmpeg, yt-dlp, aria2c)
  3. Collecting TMDB API key
  4. Collecting OpenSubtitles API key (optional)
  5. Setting a download directory
  6. Choosing a theme
  7. Writing a .env file and settings.json stub
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── Rich / prompt_toolkit may not be installed on the very first run,
#    so we fall back to plain print if needed. ─────────────────────────────────
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box as rbox
    _console = Console()
    def _print(msg, style=""):
        _console.print(msg, style=style if style else None)
    def _input(prompt):
        return _console.input(prompt)
except ImportError:
    def _print(msg, *a, **kw):
        # Strip Rich markup
        import re
        print(re.sub(r"\[/?[^\]]*\]", "", str(msg)))
    def _input(prompt):
        import re
        return input(re.sub(r"\[/?[^\]]*\]", "", str(prompt)))

# ── Paths ─────────────────────────────────────────────────────────────────────
_CLI_DIR   = Path(__file__).resolve().parents[2]   # .../cli/
_ROOT_DIR  = _CLI_DIR.parent                        # project root
_ENV_FILE  = _ROOT_DIR / ".env"
_DATA_DIR  = Path(os.path.expanduser("~/.cinema-cli"))
_FIRST_RUN_FLAG = _DATA_DIR / ".setup_done"

INSTALL_HINTS = {
    "mpv": {
        "windows": "winget install mpv  OR  choco install mpv  OR  https://mpv.io/installation/",
        "darwin":  "brew install mpv",
        "linux":   "sudo apt install mpv  OR  sudo dnf install mpv",
    },
    "ffmpeg": {
        "windows": "winget install ffmpeg  OR  choco install ffmpeg",
        "darwin":  "brew install ffmpeg",
        "linux":   "sudo apt install ffmpeg  OR  sudo dnf install ffmpeg",
    },
    "yt-dlp": {
        "windows": "pip install yt-dlp   (already in requirements.txt)",
        "darwin":  "brew install yt-dlp  OR  pip install yt-dlp",
        "linux":   "pip install yt-dlp   OR  sudo apt install yt-dlp",
    },
    "aria2c": {
        "windows": "winget install aria2  OR  choco install aria2  (optional – speeds up downloads)",
        "darwin":  "brew install aria2  (optional)",
        "linux":   "sudo apt install aria2  (optional)",
    },
}

_PLAT = platform.system().lower()   # "windows" | "darwin" | "linux"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_tool(name: str) -> tuple[bool, str]:
    """Return (found, version_string)."""
    path = shutil.which(name)
    if not path:
        return False, ""
    try:
        r = subprocess.run([name, "--version"], capture_output=True, text=True, timeout=5)
        ver = (r.stdout or r.stderr or "").strip().splitlines()[0][:60]
    except Exception:
        ver = path
    return True, ver


def _hint(tool: str) -> str:
    plat = "darwin" if _PLAT == "darwin" else ("windows" if _PLAT == "windows" else "linux")
    return INSTALL_HINTS.get(tool, {}).get(plat, "See project README")


def _write_env(values: dict[str, str]):
    """Merge key=value pairs into the .env file without destroying existing entries."""
    existing: dict[str, str] = {}
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    existing.update(values)
    lines = []
    for k, v in existing.items():
        lines.append(f"{k}={v}")
    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _load_env_key(key: str) -> str:
    """Read a single key from the .env file (no dotenv import needed here)."""
    if _ENV_FILE.exists():
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(key + "="):
                return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# ── Section banner ────────────────────────────────────────────────────────────

def _banner(title: str):
    _print(f"\n[bold cyan]{'─' * 60}[/bold cyan]")
    _print(f"[bold white]  {title}[/bold white]")
    _print(f"[bold cyan]{'─' * 60}[/bold cyan]\n")


# ── Individual steps ──────────────────────────────────────────────────────────

def _step_python():
    _banner("Step 1 / 6 — Python version")
    major, minor = sys.version_info[:2]
    if (major, minor) >= (3, 9):
        _print(f"[green]✓  Python {major}.{minor} — OK[/green]")
    else:
        _print(f"[red]✗  Python {major}.{minor} — Cinema CLI requires Python 3.9 or newer.[/red]")
        _print("[yellow]  Please upgrade Python and re-run setup.[/yellow]")
        sys.exit(1)


def _step_tools() -> dict[str, bool]:
    _banner("Step 2 / 6 — External tools")

    tools = [
        ("mpv",    True,  "Required for video playback"),
        ("ffmpeg", True,  "Required for subtitle embedding + audio/video muxing"),
        ("yt-dlp", True,  "Required for downloading streams"),
        ("aria2c", False, "Optional — boosts non-HLS download speeds"),
    ]

    status: dict[str, bool] = {}
    missing_required: list[str] = []

    for name, required, desc in tools:
        found, ver = _check_tool(name)
        status[name] = found
        icon  = "[green]✓[/green]" if found else ("[red]✗[/red]" if required else "[yellow]~[/yellow]")
        label = "[green]Found[/green]"  if found else ("[red]MISSING[/red]" if required else "[dim]not found (optional)[/dim]")
        _print(f"  {icon}  {name:<10} {label}  [dim]{ver if found else desc}[/dim]")
        if not found:
            if required:
                missing_required.append(name)
                _print(f"     [yellow]→  Install: {_hint(name)}[/yellow]")
            else:
                _print(f"     [dim]→  Install (optional): {_hint(name)}[/dim]")

    if missing_required:
        _print(
            f"\n[bold red]  {len(missing_required)} required tool(s) are missing: "
            f"{', '.join(missing_required)}[/bold red]"
        )
        choice = _input(
            "\n[bold yellow]Continue anyway? You can install tools later. (y/N): [/bold yellow]"
        ).strip().lower()
        if choice not in ("y", "yes"):
            _print("[dim]Setup cancelled. Install the missing tools and re-run.[/dim]")
            sys.exit(0)

    return status


def _step_tmdb_key():
    _banner("Step 3 / 6 — TMDB API key")
    _print(
        "  Cinema CLI uses The Movie Database (TMDB) for movie/TV metadata.\n"
        "  Get a free API key at: [bold cyan]https://www.themoviedb.org/settings/api[/bold cyan]\n"
        "  (Register for free → Account Settings → API → Request API key → Developer)\n"
    )

    existing = _load_env_key("TMDB_API_KEY")
    if existing and len(existing) > 10:
        _print(f"[green]✓  TMDB key already configured: {existing[:8]}…[/green]")
        change = _input("  Replace it? (y/N): ").strip().lower()
        if change not in ("y", "yes"):
            return existing

    while True:
        key = _input("  Paste your TMDB API key (or press Enter to skip): ").strip()
        if not key:
            _print("[yellow]  Skipped — you can add TMDB_API_KEY to .env later.[/yellow]")
            return ""
        if len(key) >= 20:
            _write_env({"TMDB_API_KEY": key})
            _print("[green]  ✓  TMDB key saved.[/green]")
            return key
        _print("[red]  That doesn't look like a valid API key. Try again.[/red]")


def _step_opensubs_key():
    _banner("Step 4 / 6 — OpenSubtitles API key  (optional)")
    _print(
        "  OpenSubtitles provides fallback subtitles when a source has none.\n"
        "  Get a free API key at: [bold cyan]https://www.opensubtitles.com/consumers[/bold cyan]\n"
        "  (You can skip this — most sources already include subtitles.)\n"
    )

    existing = _load_env_key("OPENSUBTITLES_API_KEY")
    if existing and len(existing) > 5:
        _print(f"[green]✓  OpenSubtitles key already configured: {existing[:8]}…[/green]")
        change = _input("  Replace it? (y/N): ").strip().lower()
        if change not in ("y", "yes"):
            return existing

    key = _input("  Paste your OpenSubtitles API key (or press Enter to skip): ").strip()
    if key:
        _write_env({"OPENSUBTITLES_API_KEY": key})
        _print("[green]  ✓  OpenSubtitles key saved.[/green]")
    else:
        _print("[dim]  Skipped — subtitles from source providers will still work.[/dim]")
    return key


def _step_download_dir() -> str:
    _banner("Step 5 / 6 — Download directory")
    default = str(Path.home() / "Downloads" / "CinemaCLI")
    _print(f"  Where should Cinema CLI save downloaded movies and episodes?\n"
           f"  [dim]Default: {default}[/dim]\n")

    choice = _input(f"  Press Enter to use default, or type a path: ").strip()
    directory = choice if choice else default
    directory = os.path.expanduser(directory)
    try:
        os.makedirs(directory, exist_ok=True)
        _print(f"[green]  ✓  Download directory: {directory}[/green]")
    except Exception as e:
        _print(f"[yellow]  Could not create directory: {e}  (will be created on first download)[/yellow]")
    return directory


def _step_theme() -> str:
    _banner("Step 6 / 6 — Theme")
    themes = ["cinema", "blue", "purple", "green", "gold", "teal", "rose", "sunset", "mint"]
    _print("  Available themes:")
    for i, t in enumerate(themes, 1):
        _print(f"    [dim]{i}.[/dim] {t.capitalize()}")
    _print("")

    choice = _input("  Enter theme number or name (default: cinema): ").strip().lower()
    selected = "cinema"
    if choice.isdigit() and 1 <= int(choice) <= len(themes):
        selected = themes[int(choice) - 1]
    elif choice in themes:
        selected = choice

    _print(f"[green]  ✓  Theme set to: {selected.capitalize()}[/green]")
    return selected


def _write_settings(download_dir: str, theme: str):
    """Write (or merge) settings into ~/.cinema-cli/settings.json."""
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings_path = _DATA_DIR / "settings.json"
    existing: dict = {}
    if settings_path.exists():
        try:
            with open(settings_path, encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    existing.setdefault("backend", os.getenv("BACKEND_URL", "http://localhost:3000"))
    existing["library_dir"]   = download_dir
    existing["theme"]          = theme
    if not existing.get("preferred_subtitle"):
        existing["preferred_subtitle"] = "ar"
    if not existing.get("preferred_subtitle_langs"):
        existing["preferred_subtitle_langs"] = [existing["preferred_subtitle"]]

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)


# ── Public entry point ────────────────────────────────────────────────────────

def is_first_run() -> bool:
    """Return True if the user has never completed setup."""
    return not _FIRST_RUN_FLAG.exists()


def mark_setup_done():
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    _FIRST_RUN_FLAG.write_text("ok", encoding="utf-8")


def run_wizard(force: bool = False):
    """
    Run the interactive first-run wizard.
    Skips automatically if setup has already been completed
    (unless *force* is True, e.g. called via --setup flag).
    """
    if not force and not is_first_run():
        return

    try:
        _print("\n")
        _print(
            "[bold magenta]╔══════════════════════════════════════════════════╗[/bold magenta]\n"
            "[bold magenta]║         Cinema CLI — First-Run Setup             ║[/bold magenta]\n"
            "[bold magenta]╚══════════════════════════════════════════════════╝[/bold magenta]"
        )
        _print(
            "\n  Welcome! This one-time wizard takes ~2 minutes and sets up everything\n"
            "  you need to start streaming and downloading movies & TV shows.\n"
            "  You can skip any optional step by pressing Enter.\n"
        )
        _input("  Press Enter to begin… ")

        _step_python()
        _step_tools()
        tmdb_key   = _step_tmdb_key()
        _step_opensubs_key()
        dl_dir     = _step_download_dir()
        theme      = _step_theme()

        _write_settings(dl_dir, theme)
        mark_setup_done()

        _print(
            "\n[bold green]╔══════════════════════════════════════════════╗[/bold green]"
            "\n[bold green]║  ✓  Setup complete! Launching Cinema CLI…   ║[/bold green]"
            "\n[bold green]╚══════════════════════════════════════════════╝[/bold green]\n"
        )
        if not tmdb_key:
            _print(
                "[yellow]  Reminder: add your TMDB_API_KEY to .env to unlock full metadata.[/yellow]\n"
            )
        time.sleep(1.5)

    except KeyboardInterrupt:
        _print("\n[yellow]  Setup interrupted. Run with --setup to try again.[/yellow]")
        sys.exit(0)


if __name__ == "__main__":
    run_wizard(force=True)
