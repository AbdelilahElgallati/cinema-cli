import json
import os
import shutil
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

BIN_DIR = Path.home() / ".cinema-cli" / "bin"


def _download_file(url, dest):
    """Download a file with a simple progress indication."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cinema-cli-bootstrapper"})
        with urllib.request.urlopen(req) as response, open(dest, "wb") as out_file:
            shutil.copyfileobj(response, out_file)
        return True
    except Exception as e:
        print(f"Failed to download {url}: {e}")
        return False


def get_latest_github_asset(repo, patterns):
    """Fetch the latest release asset URL from GitHub matching all patterns."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "cinema-cli-bootstrapper"})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read())
            for asset in data["assets"]:
                name = asset["name"].lower()
                if all(p.lower() in name for p in patterns):
                    return asset["browser_download_url"]
    except Exception:
        pass
    return None


def _extract_binary(archive_path, bin_name):
    """Extract a specific binary from a zip or tar archive."""
    try:
        if str(archive_path).endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zip_ref:
                for file in zip_ref.namelist():
                    if (
                        file.endswith(f"/{bin_name}")
                        or file.endswith(f"\\{bin_name}")
                        or file == bin_name
                        or file.endswith(f"/{bin_name}.exe")
                        or file.endswith(f"\\{bin_name}.exe")
                        or file == f"{bin_name}.exe"
                    ):
                        target_path = BIN_DIR / os.path.basename(file)
                        with zip_ref.open(file) as source, open(target_path, "wb") as target:
                            shutil.copyfileobj(source, target)
                        return target_path
        else:  # assume tar
            with tarfile.open(archive_path, "r:*") as tar_ref:
                for member in tar_ref.getmembers():
                    if member.name.endswith(f"/{bin_name}") or member.name == bin_name:
                        member.name = os.path.basename(member.name)
                        tar_ref.extract(member, BIN_DIR)
                        return BIN_DIR / member.name
    except Exception:
        pass
    return None


def bootstrap_dependencies():
    """Check for and download missing dependencies to ~/.cinema-cli/bin/."""
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    bin_path = str(BIN_DIR)

    # Add to PATH for the current session
    if bin_path not in os.environ.get("PATH", ""):
        os.environ["PATH"] = bin_path + os.pathsep + os.environ.get("PATH", "")

    os_name = sys.platform

    # 1. yt-dlp (Direct binary)
    if not shutil.which("yt-dlp"):
        url = None
        if os_name == "win32":
            url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
        elif os_name == "darwin":
            url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos"
        else:
            url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"

        dest = BIN_DIR / ("yt-dlp.exe" if os_name == "win32" else "yt-dlp")
        if _download_file(url, dest):
            os.chmod(dest, 0o755)

    # 2. aria2c (Zip/Tar)
    if not shutil.which("aria2c"):
        patterns = ["aria2"]
        if os_name == "win32":
            patterns.extend(["win", "64bit", ".zip"])
        elif os_name == "darwin":
            patterns.extend(["osx", ".tar.gz"])
        else:
            patterns.extend(["linux", "64bit", ".tar.gz"])

        url = get_latest_github_asset("aria2/aria2", patterns)
        if url:
            tmp_archive = BIN_DIR / ("aria2.zip" if os_name == "win32" else "aria2.tar.gz")
            if _download_file(url, tmp_archive):
                ext_path = _extract_binary(tmp_archive, "aria2c")
                if ext_path:
                    os.chmod(ext_path, 0o755)
                tmp_archive.unlink(missing_ok=True)

    # 3. ffmpeg (Zip/Tar)
    if not shutil.which("ffmpeg"):
        patterns = ["ffmpeg"]
        if os_name == "win32":
            patterns.extend(["win64", "gpl", ".zip"])
        elif os_name == "darwin":
            pass  # Hard to find official static builds for mac on GH
        else:
            patterns.extend(["linux64", "gpl", ".tar.xz"])

        url = get_latest_github_asset("BtbN/FFmpeg-Builds", patterns)
        if url:
            ext = ".zip" if os_name == "win32" else ".tar.xz"
            tmp_archive = BIN_DIR / f"ffmpeg{ext}"
            if _download_file(url, tmp_archive):
                f1 = _extract_binary(tmp_archive, "ffmpeg")
                f2 = _extract_binary(tmp_archive, "ffprobe")
                if f1:
                    os.chmod(f1, 0o755)
                if f2:
                    os.chmod(f2, 0o755)
                tmp_archive.unlink(missing_ok=True)

    # 4. mpv (Zip)
    if not shutil.which("mpv"):
        if os_name == "win32":
            url = get_latest_github_asset("shinchiro/mpv-winbuild-cmake", ["mpv-x86_64", ".zip"])
            if url:
                tmp_archive = BIN_DIR / "mpv.zip"
                if _download_file(url, tmp_archive):
                    ext_path = _extract_binary(tmp_archive, "mpv")
                    if ext_path:
                        os.chmod(ext_path, 0o755)
                    tmp_archive.unlink(missing_ok=True)
        # Note: Linux and macOS mpv are typically installed via package managers.
        # Direct binary distribution for these platforms is non-standard on GitHub.

    return bin_path
