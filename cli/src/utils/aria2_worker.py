import re
import subprocess
import sys


class Aria2Worker:
    """Wrap aria2c subprocess execution and progress parsing callbacks."""

    def __init__(self, logger):
        self._logger = logger

    def run(self, cmd, task, is_running, on_connecting, on_progress):
        try:
            _popen_kw = {}
            if sys.platform == "win32":
                _popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                **_popen_kw,
            )

            on_connecting()

            while True:
                line = process.stdout.readline()
                if not line:
                    break
                stripped = line.strip()
                if "(%]" in stripped or "%)" in stripped:
                    pct_match = re.search(r"\((\d+)%\)", stripped)
                    speed_match = re.search(r"DL:([\d.]+[KMG]i?B)", stripped)
                    eta_match = re.search(r"ETA:(\w+)", stripped)

                    on_progress(
                        percent=float(pct_match.group(1)) if pct_match else None,
                        speed=(speed_match.group(1) + "/s") if speed_match else None,
                        eta=eta_match.group(1) if eta_match else None,
                    )

                if not is_running():
                    process.terminate()
                    return False

            process.wait()
            return process.returncode == 0
        except Exception as exc:
            self._logger(f"aria2c execution failed: {exc}", "ERROR")
            return False
