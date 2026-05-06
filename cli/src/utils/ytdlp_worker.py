import codecs
import re
import subprocess
import sys
import threading
import time
from collections import deque

LINE_SPLIT_RE = re.compile(r"\r?\n")
ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
CHUNK_SIZE_BYTES = 8192


class YtDlpWorker:
    """Wrap yt-dlp subprocess execution and output parsing callbacks."""

    def __init__(self, logger):
        self._logger = logger

    def run(self, cmd, task, is_running, on_line, on_connecting):
        process = None
        reader_thread = None
        recent_lines = deque(maxlen=20)
        shared = {
            "buffer": bytearray(),
            "closed": False,
            "lock": threading.Lock(),
        }
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        carry_text = ""

        try:
            _popen_kw = {}
            if sys.platform == "win32":
                _popen_kw["creationflags"] = subprocess.CREATE_NO_WINDOW

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                **_popen_kw,
            )

            def _reader():
                try:
                    while True:
                        chunk = process.stdout.read(CHUNK_SIZE_BYTES)
                        if not chunk:
                            break
                        with shared["lock"]:
                            shared["buffer"].extend(chunk)
                finally:
                    with shared["lock"]:
                        shared["closed"] = True
                    process.stdout.close()

            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()
            on_connecting()

            start_time = time.time()
            max_duration = 7200
            stall_timeout = 120
            bytes_stall_timeout = 90
            last_progress_time = time.time()
            last_output_time = time.time()
            last_bytes_time = None
            last_bytes_seen = 0
            last_progress_pct = 0
            download_started = False
            muxing_started = False

            while True:
                now = time.time()
                if now - start_time > max_duration:
                    self._logger("Download timeout exceeded", "ERROR")
                    self._terminate(process, reader_thread)
                    return False, list(recent_lines)

                effective_stall_timeout = 600 if muxing_started else stall_timeout
                if now - last_output_time > effective_stall_timeout:
                    self._logger(
                        f"Download stalled (no output for {effective_stall_timeout//60} minutes)",
                        "ERROR",
                    )
                    self._terminate(process, reader_thread)
                    return False, list(recent_lines)

                if download_started and not muxing_started and last_bytes_time is not None:
                    cur_bytes = task.get("_bytes_downloaded", 0)
                    cur_progress = task.get("progress", 0)
                    if cur_bytes > last_bytes_seen or cur_progress > last_progress_pct:
                        last_bytes_seen = cur_bytes
                        last_progress_pct = cur_progress
                        last_bytes_time = now
                    elif now - last_bytes_time > bytes_stall_timeout:
                        self._logger(
                            f"Byte-level stall: 0 bytes received in {bytes_stall_timeout}s after download started",
                            "WARNING",
                        )
                        self._terminate(process, reader_thread)
                        return False, list(recent_lines)

                chunk = self._drain_shared_bytes(shared)
                if chunk:
                    last_output_time = now
                    carry_text, states = self._process_chunk(
                        chunk=chunk,
                        decoder=decoder,
                        carry_text=carry_text,
                        recent_lines=recent_lines,
                        on_line=on_line,
                    )
                    for state in states:
                        if state.get("progress_updated"):
                            last_progress_time = now
                        if state.get("download_started"):
                            download_started = True
                            last_bytes_time = now
                            last_bytes_seen = 0
                        if state.get("muxing_started"):
                            muxing_started = True
                            last_progress_time = now

                ret = process.poll()
                if ret is not None and not chunk and self._is_reader_closed(shared):
                    break

                if not is_running():
                    self._terminate(process, reader_thread)
                    return False, list(recent_lines)

                time.sleep(0.1)

            if reader_thread:
                reader_thread.join(timeout=3)

            final_chunk = self._drain_shared_bytes(shared)
            if final_chunk:
                carry_text, _ = self._process_chunk(
                    chunk=final_chunk,
                    decoder=decoder,
                    carry_text=carry_text,
                    recent_lines=recent_lines,
                    on_line=on_line,
                )

            tail_text = decoder.decode(b"", final=True)
            if tail_text:
                carry_text += tail_text

            if carry_text.strip():
                for line in LINE_SPLIT_RE.split(carry_text):
                    cleaned = ANSI_ESCAPE_RE.sub("", (line or "").strip())
                    if cleaned:
                        recent_lines.append(cleaned)
                        on_line(cleaned)

            return ret == 0, list(recent_lines)
        except Exception as exc:
            self._logger(f"yt-dlp execution failed: {exc}", "ERROR")
            return False, list(recent_lines)
        finally:
            if process and process.poll() is None:
                self._terminate(process, reader_thread)

    @staticmethod
    def _drain_shared_bytes(shared):
        with shared["lock"]:
            if not shared["buffer"]:
                return b""
            data = bytes(shared["buffer"])
            shared["buffer"].clear()
            return data

    @staticmethod
    def _is_reader_closed(shared):
        with shared["lock"]:
            return bool(shared["closed"])

    @staticmethod
    def _process_chunk(chunk, decoder, carry_text, recent_lines, on_line):
        decoded = decoder.decode(chunk)
        text = carry_text + decoded
        if not text:
            return "", []

        parts = LINE_SPLIT_RE.split(text)
        if text.endswith("\n") or text.endswith("\r"):
            next_carry = ""
        else:
            next_carry = parts.pop() if parts else ""

        states = []
        for line in parts:
            cleaned = ANSI_ESCAPE_RE.sub("", (line or "").strip())
            if not cleaned:
                continue
            recent_lines.append(cleaned)
            states.append(on_line(cleaned) or {})

        return next_carry, states

    @staticmethod
    def _terminate(process, reader_thread):
        try:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        except Exception:
            pass
        if reader_thread:
            reader_thread.join(timeout=3)
