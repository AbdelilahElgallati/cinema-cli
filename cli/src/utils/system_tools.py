import os
import shutil
import subprocess
import sys
from pathlib import Path


def _candidate_directories() -> list[str]:
    dirs: list[str] = []

    path_env = os.environ.get("PATH", "")
    for part in path_env.split(os.pathsep):
        if part:
            dirs.append(part)

    # Add active Python environment script directories explicitly.
    py_dir = Path(sys.executable).resolve().parent
    dirs.append(str(py_dir))
    dirs.append(str(py_dir / "Scripts"))
    dirs.append(str(py_dir / "bin"))

    if os.name == "nt":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
        local_appdata = os.environ.get("LOCALAPPDATA", "")
        user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
        program_data = os.environ.get("ProgramData", r"C:\ProgramData")

        # Common installation roots and package managers
        dirs.extend(
            [
                # Custom root installs
                r"C:\mpv",
                r"C:\ffmpeg\bin",
                r"C:\yt-dlp",
                # Package managers
                os.path.join(program_data, "chocolatey", "bin"),
                os.path.join(user_profile, "scoop", "shims"),
                # Standard Program Files locations
                os.path.join(program_files, "mpv"),
                os.path.join(program_files_x86, "mpv"),
                os.path.join(program_files, "ffmpeg", "bin"),
                os.path.join(program_files_x86, "ffmpeg", "bin"),
                os.path.join(program_files, "VideoLAN", "VLC"),
                os.path.join(program_files_x86, "VideoLAN", "VLC"),
            ]
        )
        if local_appdata:
            dirs.extend(
                [
                    os.path.join(local_appdata, "Microsoft", "WinGet", "Packages"),
                    os.path.join(local_appdata, "Programs", "Python", "Python311", "Scripts"),
                    os.path.join(local_appdata, "Programs", "Python", "Python312", "Scripts"),
                    os.path.join(local_appdata, "Programs", "Python", "Python313", "Scripts"),
                ]
            )

    # Deduplicate while preserving order.
    deduped: list[str] = []
    seen = set()
    for item in dirs:
        if not item:
            continue
        key = str(item).lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(str(item))
    return deduped


def find_executable(name: str, aliases: list[str] | None = None) -> str | None:  # NOSONAR
    """Resolve a tool executable path across PATH, venv scripts, and common OS paths."""
    names = [name] + (aliases or [])

    # 1. Standard shutil.which lookup (handles PATH and current venv)
    for candidate in names:
        found = shutil.which(candidate)
        if found:
            return found

    exts = [""]
    if os.name == "nt":
        # Order matters: .exe preferred over scripts
        exts = [".exe", ".cmd", ".bat", ""]

    # 2. Check candidate directories directly
    candidate_dirs = _candidate_directories()
    for folder in candidate_dirs:
        if not os.path.isdir(folder):
            continue
        for candidate in names:
            for ext in exts:
                path = os.path.join(folder, f"{candidate}{ext}")
                if os.path.isfile(path):
                    return path

    # 3. Deep search for stubborn Windows installations (WinGet, etc.)
    if os.name == "nt":
        # Only search folders that actually exist and might contain our tools
        search_roots = [
            d
            for d in candidate_dirs
            if ("WinGet" in d or "Packages" in d or "scoop" in d) and os.path.isdir(d)
        ]
        for root_folder in search_roots:
            try:
                # Limit depth to 3 to prevent hanging on massive drives
                for root, dirs, files in os.walk(root_folder):
                    if root.count(os.sep) - root_folder.count(os.sep) > 3:
                        del dirs[:]  # don't go deeper
                        continue

                    file_set = {f.lower() for f in files}
                    for candidate in names:
                        for ext in [".exe", ".cmd", ".bat"]:
                            target = f"{candidate}{ext}".lower()
                            if target in file_set:
                                return os.path.join(root, f"{candidate}{ext}")
            except Exception:
                continue

    return None


def is_tool_available(name: str, aliases: list[str] | None = None) -> bool:
    return find_executable(name, aliases=aliases) is not None


def get_tool_version(executable: str, args: list[str] | None = None) -> str:
    cmd = [executable] + (args or ["--version"])
    try:
        run_kwargs = {"capture_output": True, "text": True, "timeout": 5}
        if os.name == "nt":
            run_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        result = subprocess.run(cmd, **run_kwargs)
        output = (result.stdout or result.stderr or "").strip()
        if output:
            return output.splitlines()[0][:80]
    except Exception:
        pass
    return executable
