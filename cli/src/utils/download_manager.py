import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib3
import uuid
import requests
import queue
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from collections import deque

from src.config import DOWNLOAD_LOG, SUCCESS, TEXT, WARNING, console
from src.utils import app_logger
from src.utils.app_logger import log_event
from src.utils.source_strategy import filter_sources_for_quality
from src.utils.storage import load_json_data, save_json_data
from src.utils.system_tools import find_executable, is_tool_available
from src.utils.utils import sanitize_filename
from src.utils.subtitles import fetch_subtitles

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DOWNLOADS_FILE = os.path.expanduser("~/.cinema-cli-downloads.json")
APP_NAME = "Cinema CLI"
YTDLP_TEMP_SUFFIXES = (".part", ".ytdl", ".temp")
MUX_TAGS = ("[ffmpeg]", "[Merger]", "[FixupM3u8]")
DESTINATION_TAG = "Destination:"
MIN_FREE_SPACE_BYTES = 1024 * 1024 * 1024


def _vtt_to_srt(vtt_text: str) -> str:  # NOSONAR
    """Convert WebVTT subtitle text to SRT format.

    Handles:
      - Stripping the WEBVTT header / NOTE blocks / STYLE blocks
      - Converting VTT timestamp format (HH:MM:SS.mmm) to SRT (HH:MM:SS,mmm)
      - Re-numbering cues sequentially (SRT requires numeric indices)
      - Preserving cue text (including multi-line)
    """
    lines = vtt_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    srt_blocks = []
    cue_idx = 0
    i = 0

    # Skip BOM and WEBVTT header
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("WEBVTT"):
            i += 1
            # Skip any lines until the first blank line after the header
            while i < len(lines) and lines[i].strip():
                i += 1
            break
        if not stripped:
            i += 1
            continue
        break

    while i < len(lines):
        line = lines[i].strip()

        # Skip blank lines, NOTE blocks, STYLE blocks, and cue identifiers
        if not line:
            i += 1
            continue
        if line.startswith("NOTE") or line.startswith("STYLE"):
            # Skip until next blank line
            i += 1
            while i < len(lines) and lines[i].strip():
                i += 1
            continue

        # Detect timestamp line: "HH:MM:SS.mmm --> HH:MM:SS.mmm"
        if "-->" in line:
            # Convert '.' to ',' in timestamps for SRT
            # Handle both HH:MM:SS.mmm and MM:SS.mmm formats
            ts_line = re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", line)
            ts_line = re.sub(r"(\d{2}:\d{2})\.(\d{3})", r"\1,\2", ts_line)
            # Strip VTT position/alignment settings after timestamps
            ts_line = re.sub(r"([\d:,]+\s*-->\s*[\d:,]+)\s+.*", r"\1", ts_line)
            # Ensure HH:MM:SS format only for MM:SS,mmm timestamps.
            m = re.match(r"\s*([\d:,]+)\s*-->\s*([\d:,]+)\s*$", ts_line)
            if m:
                start_ts, end_ts = m.group(1), m.group(2)
                if start_ts.count(":") == 1:
                    start_ts = f"00:{start_ts}"
                if end_ts.count(":") == 1:
                    end_ts = f"00:{end_ts}"
                ts_line = f"{start_ts} --> {end_ts}"
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].rstrip())
                i += 1
            if text_lines:
                cue_idx += 1
                srt_blocks.append(f"{cue_idx}\n{ts_line}\n" + "\n".join(text_lines))
        else:
            # Could be a cue identifier line (ignored in SRT output)
            i += 1

    return "\n\n".join(srt_blocks) + "\n" if srt_blocks else vtt_text


class DownloadManager:
    def __init__(self, max_workers=1, downloads_dir=None, api_client=None, settings=None):
        self.queue = load_json_data(DOWNLOADS_FILE) or []
        # Reset any 'downloading' status to 'pending' on startup
        for task in self.queue:
            if task["status"] == "downloading":
                task["status"] = "pending"
        self.running = False
        # Sequential: only 1 worker at a time
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.lock = threading.Lock()
        self.active_tasks = {}  # task_id -> future
        self._current_task_id = None  # Track the single active download
        self.api_client = api_client
        self.settings = settings or {}
        self.downloads_dir = downloads_dir or os.path.join(os.path.expanduser("~"), "Downloads", "cinema-cli")
        # Use user's temp directory instead of os.getcwd() to avoid system32 issues
        self.temp_dir = os.path.join(tempfile.gettempdir(), "cinema-cli-temp")
        
        # Throttled save mechanism to reduce disk I/O
        self._last_save_time = 0
        self._save_interval = 2.0  # Min seconds between saves (raised from 0.5 → less lock contention)
        self._pending_save = False
        
        # Singleton HTTP session pool — reused across all direct-download calls
        # to avoid the overhead of creating a new session+adapter on every request.
        self._http_session = None
        self._http_session_lock = threading.Lock()

    def start(self):
        if not self.running:
            self.running = True
            threading.Thread(target=self._queue_listener, daemon=True).start()

    def stop(self):
        self.running = False
        # Flush any pending saves
        try:
            self._flush_save()
        except Exception as e:
            app_logger.debug(f"Suppressed error in download_manager: {e}", exc_info=True)
        # Cancel pending futures
        for tid, future in self.active_tasks.items():
            try:
                if not future.done():
                    future.cancel()
            except Exception as e:
                app_logger.debug(f"Suppressed error in download_manager (cancel future): {e}", exc_info=True)
        self.executor.shutdown(wait=False)
        # Close the shared HTTP session to release TCP connections
        try:
            with self._http_session_lock:
                if self._http_session is not None:
                    self._http_session.close()
                    self._http_session = None
        except Exception as e:
            app_logger.debug(f"Suppressed error in download_manager: {e}", exc_info=True)

    def _log(self, message, level="INFO"):
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(DOWNLOAD_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] [{level}] {message}\n")
            log_event("download", message, level=level)
        except Exception as e:
            app_logger.debug(f"Suppressed error in download_manager: {e}", exc_info=True)

    def _estimate_required_space(self, task):
        """Estimate required bytes to safely queue a download."""
        quality = str(task.get("quality") or "").lower()
        runtime_min = 110
        meta = task.get("meta") or {}
        if isinstance(meta, dict):
            try:
                runtime_val = int(meta.get("runtime") or 0)
                if runtime_val > 0:
                    runtime_min = runtime_val
                elif str(meta.get("type") or "").lower() == "tv":
                    runtime_min = 45
            except (TypeError, ValueError):
                pass

        bitrate_mbps_map = {
            "4k": 20.0,
            "2160": 20.0,
            "1080": 8.0,
            "720": 4.0,
            "480": 2.0,
            "360": 1.0,
            "240": 0.5,
        }
        bitrate_mbps = 8.0
        for key, value in bitrate_mbps_map.items():
            if key in quality:
                bitrate_mbps = value
                break

        estimated_bytes = int(runtime_min * 60 * bitrate_mbps * 125000 * 1.5)
        safety_headroom = 300 * 1024 * 1024
        return max(MIN_FREE_SPACE_BYTES, estimated_bytes + safety_headroom)

    def _has_sufficient_disk_space(self, task):
        """Return (ok, required, free)."""
        try:
            os.makedirs(self.downloads_dir, exist_ok=True)
            free_bytes = shutil.disk_usage(self.downloads_dir).free
            required = self._estimate_required_space(task)
            return free_bytes >= required, required, free_bytes
        except Exception:
            return True, 0, 0

    def add_task(self, url, filename, title, subtitles=None, headers=None, meta=None, fallback_sources=None, api_params=None, preferred_sub_lang='ar', include_all_subs=True, preferred_sub_langs=None, fallback_sub_langs=None, quality=None):
        task = {
            "id": str(uuid.uuid4()),
            "url": url,
            "filename": filename,
            "title": title,
            "subtitles": subtitles,
            "preferred_sub_lang": preferred_sub_lang,
            "include_all_subs": include_all_subs,
            "preferred_sub_langs": preferred_sub_langs or ([preferred_sub_lang] if preferred_sub_lang else ["ar"]),
            "fallback_sub_langs": fallback_sub_langs,
            "headers": headers,
            "meta": meta,
            "quality": quality,
            "fallback_sources": fallback_sources or [],
            "api_params": api_params,
            "speed_limit_mb": self.settings.get("download_speed_limit", 0),
            "status": "pending",
            "progress": 0,
            "speed": "0 B/s",
            "eta": "00:00",
            "total_size": "Unknown",
            "downloaded": "0 B",
            "error_log": "",
            "retries": 0,
            "added_at": time.time(),
        }

        has_space, required, free = self._has_sufficient_disk_space(task)
        if not has_space:
            msg = (
                f"Insufficient disk space for '{title}'. "
                f"Need ~{self._bytes_to_human(required)} free, have {self._bytes_to_human(free)}."
            )
            self._log(msg, level="WARNING")
            console.print(f"[bold red]{msg}[/bold red]")
            return False

        with self.lock:
            self.queue.append(task)
            self._save()
        console.print(f"[green]Added to download queue: {title}[/green]")
        return True

    def _save(self, force=False):
        """Save queue state with throttling to reduce disk I/O."""
        now = time.time()
        if not force and (now - self._last_save_time) < self._save_interval:
            self._pending_save = True
            return
        save_json_data(DOWNLOADS_FILE, self.queue)
        self._last_save_time = now
        self._pending_save = False
    
    def _flush_save(self):
        """Force save if there are pending changes."""
        if self._pending_save:
            save_json_data(DOWNLOADS_FILE, self.queue)
            self._pending_save = False

    def _build_session(self):
        """Return (or lazily create) a shared requests.Session with retry adapters.
        Re-using one session across all calls avoids reconnect overhead and keeps
        the connection pool warm for range-based parallel downloads.
        """
        with self._http_session_lock:
            if self._http_session is not None:
                return self._http_session
            import requests
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry

            session = requests.Session()
            try:
                retry = Retry(
                    total=5,
                    connect=3,
                    read=3,
                    backoff_factor=0.3,
                    status_forcelist=[429, 500, 502, 503, 504],
                    allowed_methods=["HEAD", "GET"],
                )
            except TypeError:
                retry = Retry(
                    total=5,
                    connect=3,
                    read=3,
                    backoff_factor=0.3,
                    status_forcelist=[429, 500, 502, 503, 504],
                    method_whitelist=["HEAD", "GET"],
                )
            adapter = HTTPAdapter(
                max_retries=retry,
                pool_connections=20,
                pool_maxsize=40,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            self._http_session = session
            return session

    def _queue_listener(self):  # NOSONAR
        """Sequential queue listener — downloads one task at a time (wait list)."""
        while self.running:
            try:
                # Clean up finished tasks first
                with self.lock:
                    finished = [
                        tid for tid, fut in self.active_tasks.items()
                        if fut.done()
                    ]
                    for tid in finished:
                        try:
                            future = self.active_tasks[tid]
                            exc = future.exception()
                            if exc:
                                self._log(f"Task {tid} crashed: {exc}", level="ERROR")
                                for t in self.queue:
                                    if t["id"] == tid:
                                        t["status"] = "error"
                                        t["error_log"] = f"Crash: {str(exc)}"
                                        break
                                self._save(force=True)
                        except Exception as cleanup_err:
                            self._log(f"Cleanup error for {tid}: {cleanup_err}", level="ERROR")
                        finally:
                            del self.active_tasks[tid]
                            if self._current_task_id == tid:
                                self._current_task_id = None

                # Only start a new task if nothing is currently downloading
                with self.lock:
                    if self._current_task_id is not None:
                        # Still downloading — wait
                        pass
                    else:
                        # Find next pending task (FIFO order by added_at)
                        pending = sorted(
                            [t for t in self.queue if t["status"] == "pending"],
                            key=lambda t: t.get("added_at", 0)
                        )
                        if pending:
                            task = pending[0]
                            task["status"] = "downloading"
                            task["_bytes_downloaded"] = 0
                            task["_bytes_total"] = 0
                            task["_dl_start_time"] = time.time()
                            task["_speed_samples"] = []  # (timestamp, bytes) for rolling speed
                            self._current_task_id = task["id"]
                            self._save()
                            future = self.executor.submit(
                                self._safe_process_task_wrapper, task
                            )
                            self.active_tasks[task["id"]] = future

            except Exception as e:
                self._log(f"Queue listener error: {e}", level="ERROR")
                time.sleep(5)

            time.sleep(0.5)

    def _safe_process_task_wrapper(self, task):
        """Isolation wrapper - ensures one task crash doesn't kill the listener."""
        try:
            self._process_task(task)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._log(f"Task {task.get('id')} crashed! \nTraceback:\n{tb}", level="ERROR")
            with self.lock:
                task["status"] = "error"
                task["error_log"] = f"CRASH: {str(e)}\nSee download.log for details."
                task["status_message"] = "CRASHED"
                self._save(force=True)
            raise  # Re-raise so future.exception() catches it

    def _process_task(self, task):  # NOSONAR
        """Robust task processing with parallel subtitle download and multiple fallback layers."""
        temp_dir = self.temp_dir
        os.makedirs(temp_dir, exist_ok=True)
        
        # Use unique temp filename to avoid collisions
        temp_id = str(uuid.uuid4())[:8]
        # Sanitize the filename to avoid path issues
        safe_fname = sanitize_filename(os.path.splitext(task['filename'])[0]) + ".mp4"
        mp4_out = os.path.join(temp_dir, f"{temp_id}_{safe_fname}")

        # Start subtitle download in background thread (parallel with video).
        # Keep fallback subtitle fetching enabled even when provider subtitles
        # are empty, unless the user explicitly disabled subtitles.
        subtitle_future = None
        _subs_enabled = str(task.get("preferred_sub_lang") or "ar").strip().lower() not in ("none", "")
        if hasattr(self, "_download_subtitles") and _subs_enabled:
            subtitle_executor = ThreadPoolExecutor(max_workers=1)
            subtitle_future = subtitle_executor.submit(self._download_subtitles, task, temp_dir)
            self._log("Started subtitle download in background", level="INFO")

        max_attempts = 3
        success = False
        last_error = ""
        failed_source_names = set()  # Track sources that already failed

        for attempt in range(max_attempts):
            if not self.running:
                return  # Graceful shutdown

            if attempt > 0:
                # Exponential backoff: 5s, 15s — gives CDN rate-limit windows time to expire
                backoff = 5 * (3 ** (attempt - 1))
                self._log(
                    f"Retry {attempt + 1}/{max_attempts} for {task['title']} (waiting {backoff}s)...", 
                    level="INFO"
                )
                time.sleep(backoff)

            try:
                # Refresh source if needed (force_refresh on retry)
                refreshed_url = self._refresh_source_if_needed(task, attempt)
                
                # If we got fresh URLs, clear failed-source blacklist — new URLs
                # from different CDN edges may work even if old ones were 429/500
                if refreshed_url and attempt > 0:
                    old_count = len(failed_source_names)
                    failed_source_names.clear()
                    if old_count:
                        self._log(f"Cleared {old_count} failed sources after URL refresh", level="INFO")
                
                # Build source list with priority
                sources_to_try = self._build_source_list(task, refreshed_url)
                
                if not sources_to_try:
                    last_error = "No valid sources"
                    continue

                # Try each source, skipping ones that already failed
                for i, source in enumerate(sources_to_try):
                    if not self.running:
                        return

                    src_key = f"{source.get('name','')}|{self._extract_url(source.get('file',''))}"
                    if src_key in failed_source_names:
                        self._log(f"Skipping previously failed source: {source.get('name')}", level="INFO")
                        continue
                        
                    success = self._try_download_source(
                        source, mp4_out, task, attempt, i
                    )
                    
                    if success:
                        break
                    else:
                        failed_source_names.add(src_key)
                        # Check if this source was rate-limited (429)
                        if task.get("_got_rate_limited"):
                            task.pop("_got_rate_limited", None)
                
                if success:
                    break
                    
            except Exception as e:
                last_error = str(e)
                self._log(f"Attempt {attempt + 1} error: {e}", level="ERROR")

        # Wait for subtitle download to complete (if started)
        if subtitle_future:
            try:
                subtitle_future.result(timeout=60)  # Max 60s wait for subtitles
                self._log("Subtitle download completed", level="INFO")
            except Exception as e:
                self._log(f"Subtitle download failed: {e}", level="WARNING")
            finally:
                subtitle_executor.shutdown(wait=False)

        # yt-dlp may produce a file with a different extension; find the actual output
        if success and not os.path.exists(mp4_out):
            mp4_out = self._find_actual_output(temp_dir, temp_id, mp4_out)
            if not mp4_out:
                success = False
                last_error = "Downloaded file not found after yt-dlp"

        # Final status update
        self._finalize_task(task, success, mp4_out, last_error)

    def _find_actual_output(self, temp_dir, temp_id, _expected_path):
        """Find the actual output file yt-dlp created (may differ in extension)."""
        # Search for files starting with our temp_id prefix
        for fname in os.listdir(temp_dir):
            if fname.startswith(temp_id) and not fname.endswith(YTDLP_TEMP_SUFFIXES):
                full = os.path.join(temp_dir, fname)
                if os.path.isfile(full) and os.path.getsize(full) > 1024 * 1024:
                    self._log(f"Found actual output: {fname}", level="INFO")
                    return full
        return None

    def _extract_url(self, value):  # NOSONAR
        def find_in_obj(obj):
            if isinstance(obj, str):
                if "http" in obj:
                    m = re.search(r"https?://[^\"\s]+", obj)
                    if m:
                        return m.group(0)
                return None
            if isinstance(obj, dict):
                for k in ["file", "url", "hls", "hls4", "source"]:
                    v = obj.get(k)
                    found = find_in_obj(v)
                    if found:
                        return found
                for v in obj.values():
                    found = find_in_obj(v)
                    if found:
                        return found
            if isinstance(obj, (list, tuple)):
                for v in obj:
                    found = find_in_obj(v)
                    if found:
                        return found
            return None

        if isinstance(value, dict):
            return find_in_obj(value) or value
        if isinstance(value, str):
            if value.strip().startswith("{"):
                try:
                    parsed = json.loads(value)
                    return find_in_obj(parsed) or value
                except Exception:
                    pass
            found = find_in_obj(value)
            return found or value
        return value

    def _download_subtitles(self, task, temp_dir):  # NOSONAR
        """Download subtitles in parallel for faster performance."""
        # If already downloaded, skip
        if task.get("subtitle_files") or task.get("subtitle_file"):
            return

        # Subtitle preferences
        preferred = (task.get("preferred_sub_lang") or "ar").strip().lower()
        include_all = bool(task.get("include_all_subs", True))
        fallback_langs = task.get("fallback_sub_langs")

        # Ordered multi-language list (primary first)
        raw_pref_langs = task.get("preferred_sub_langs") or [preferred]
        if not isinstance(raw_pref_langs, list) or not raw_pref_langs:
            raw_pref_langs = [preferred]

        def norm_lang(lang: str) -> str:
            l = (lang or "").strip().lower()
            if l in ["arabic", "ara", "ar"]:   return "ar"
            if l in ["english", "eng", "en"]:  return "en"
            if l in ["french", "fre", "fra", "fr"]:  return "fr"
            if l in ["spanish", "spa", "es"]:  return "es"
            if l in ["german", "deu", "ger", "de"]:  return "de"
            if l in ["turkish", "tur", "tr"]:  return "tr"
            if l in ["portuguese", "por", "pt"]: return "pt"
            if l in ["italian", "ita", "it"]:  return "it"
            if l in ["chinese", "zho", "chi", "zh"]: return "zh"
            if l in ["japanese", "jpn", "ja"]: return "ja"
            if l in ["korean", "kor", "ko"]:   return "ko"
            if l in ["hindi", "hin", "hi"]:    return "hi"
            return l or "und"

        def display_lang(code: str) -> str:
            m = {
                "ar": "Arabic", "en": "English", "fr": "French",
                "es": "Spanish", "de": "German", "tr": "Turkish",
                "pt": "Portuguese", "it": "Italian",
                "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
                "hi": "Hindi", "und": "Unknown",
            }
            return m.get(code, code)

        # Normalize
        preferred = norm_lang(preferred)
        if preferred == "none":
            return

        wanted = [norm_lang(l) for l in raw_pref_langs if l]
        if not wanted:
            wanted = [preferred]
        elif wanted[0] != preferred:
            wanted = [preferred] + [l for l in wanted if l != preferred]

        # Build subtitle list from API payload
        subs = task.get("subtitles") or []
        items = []
        for s in subs:
            if isinstance(s, dict) and s.get("url"):
                items.append({
                    "lang": norm_lang(s.get("lang") or s.get("language") or s.get("code")),
                    "url": s.get("url"),
                })

        # If API didn't provide subtitles, fall back to OpenSubtitles
        if not items:
            try:
                yr = sn = epn = None
                meta = task.get("meta") or {}
                if isinstance(meta, dict):
                    yr = meta.get("year")
                    sn = meta.get("season")
                    epn = meta.get("episode")

                # Request langs: wanted first, then fallback_langs, then ar+en
                langs = list(wanted) if include_all else [preferred]
                if isinstance(fallback_langs, (list, tuple)):
                    for x in fallback_langs:
                        c = str(x).strip().lower()
                        if c and c not in langs:
                            langs.append(c)
                for last in ("ar", "en"):
                    if last not in langs:
                        langs.append(last)

                subs_found = fetch_subtitles(task.get("title") or "", langs, year=yr, season=sn, episode=epn, max_per_language=3)
                if subs_found:
                    base, _ = os.path.splitext(task.get("filename") or task.get("title") or "video")
                    base = os.path.basename(base)
                    # Sort by wanted-list priority
                    def _sk(s):
                        lc = norm_lang(str(s.get("lang") or "und"))
                        try: return langs.index(lc)
                        except ValueError: return len(langs)
                    subs_found = sorted(subs_found, key=_sk)
                    downloaded = []
                    for s in subs_found:
                        lang = norm_lang(str(s.get("lang") or "und"))
                        ext = str(s.get("ext") or "srt")
                        content = s.get("content") or b""
                        if not content:
                            continue
                        decoded = None
                        if ext == "vtt":
                            try:
                                decoded = content.decode("utf-8", errors="ignore")
                                decoded = _vtt_to_srt(decoded)
                                ext = "srt"
                            except Exception:
                                decoded = None
                        sub_filename = os.path.join(temp_dir, f"{base}.{lang}.{ext}")
                        if decoded is not None:
                            with open(sub_filename, "w", encoding="utf-8-sig") as f:
                                f.write(decoded)
                        else:
                            with open(sub_filename, "wb") as f:
                                f.write(content)
                        downloaded.append({"lang": lang, "name": display_lang(lang), "path": sub_filename})
                        if not include_all:
                            break
                    if downloaded:
                        with self.lock:
                            task["subtitle_files"] = downloaded
                            task["subtitle_file"] = downloaded[0]["path"]
                            self._save(force=True)
                    return
            except Exception as e:
                self._log(f"OpenSubtitles fallback failed: {e}", level="WARNING")
            return

        # Build ordered list from source subtitles: wanted langs first (in priority order)
        ordered = []
        seen_url = set()
        seen_lang = set()
        for lang in (wanted if include_all else wanted[:1]):
            for x in items:
                if x["lang"] == lang and x["url"] not in seen_url and x["lang"] not in seen_lang:
                    ordered.append(x)
                    seen_url.add(x["url"])
                    seen_lang.add(x["lang"])
                    break

        if include_all:
            for x in items:
                if x["url"] not in seen_url and x["lang"] not in seen_lang:
                    ordered.append(x)
                    seen_url.add(x["url"])
                    seen_lang.add(x["lang"])

        if not ordered and items:
            ordered.append(items[0])

        if not ordered:
            return

        # Parallel subtitle download
        import requests
        base, _ = os.path.splitext(task.get("filename") or task.get("title") or "video")
        base = os.path.basename(base)
        
        def download_single_sub(sub):
            """Download a single subtitle file."""
            try:
                sub_url = sub["url"]
                sub_lang = sub["lang"] or "und"
                # Always save as .srt for maximum player compatibility
                sub_filename = os.path.join(temp_dir, f"{base}.{sub_lang}.srt")
                
                resp = requests.get(sub_url, timeout=15, verify=False, headers=task.get("headers") or {})  # NOSONAR
                resp.raise_for_status()
                
                # Validate: avoid HTML error pages
                content_type = (resp.headers.get("content-type") or "").lower()
                if "text/html" in content_type and len(resp.content) < 8000:
                    return None
                
                # Validate: check for actual subtitle content
                if not resp.content or len(resp.content) < 20:
                    return None
                head = resp.content[:2048].lower()
                if b"<html" in head or b"<!doctype" in head:
                    return None
                
                # Decode robustly with extended encoding list
                decoded = None
                for enc in ["utf-8", "utf-8-sig", "cp1256", "windows-1256",
                            "iso-8859-6", "iso-8859-1", "cp1252",
                            "shift_jis", "euc-kr", "gb18030", "latin-1"]:
                    try:
                        decoded = resp.content.decode(enc)
                        break
                    except (UnicodeDecodeError, LookupError):
                        continue
                
                if decoded is None:
                    decoded = resp.content.decode("utf-8", errors="ignore")
                
                # Convert VTT to SRT if needed (fixes timing issues in many players)
                if decoded.lstrip().startswith("WEBVTT") or ".vtt" in sub_url.lower():
                    decoded = _vtt_to_srt(decoded)
                
                with open(sub_filename, "w", encoding="utf-8-sig") as f:
                    f.write(decoded)
                
                return {
                    "lang": sub_lang,
                    "name": display_lang(sub_lang),
                    "path": sub_filename,
                }
            except Exception:
                return None
        
        try:
            # Download all subtitles in parallel (max 5)
            subs_to_download = ordered[:5]
            downloaded = []
            
            with ThreadPoolExecutor(max_workers=min(5, len(subs_to_download))) as executor:
                futures = {executor.submit(download_single_sub, sub): sub for sub in subs_to_download}
                for future in as_completed(futures, timeout=30):
                    result = future.result()
                    if result:
                        downloaded.append(result)
            
            # Sort by preferred language first
            if downloaded:
                downloaded.sort(key=lambda x: (0 if x["lang"] == preferred else 1, x["lang"]))
                with self.lock:
                    task["subtitle_files"] = downloaded
                    task["subtitle_file"] = downloaded[0]["path"]
                    self._save(force=True)
                self._log(f"Downloaded {len(downloaded)} subtitles in parallel", level="INFO")
                
        except Exception as e:
            self._log(f"Parallel subtitle download failed: {e}", level="WARNING")

    def _refresh_source_if_needed(self, task, attempt):
        """Refresh streaming source from API if available.
        
        On retry attempts, forces a cache bypass so the backend can return
        fresh CDN URLs (the old ones may be rate-limited or expired).
        Uses get_sources_enhanced on later retries to aggregate more providers.
        """
        if not (task.get("api_params") and self.api_client and attempt > 0):
            return None
            
        try:
            p = task["api_params"]
            self._log(f"Refreshing source (Attempt {attempt + 1}, force_refresh=True)...", level="INFO")
            
            # On retry >= 2, use enhanced fetching to aggregate more providers
            if attempt >= 2 and hasattr(self.api_client, "get_sources_enhanced"):
                data = self.api_client.get_sources_enhanced(
                    p.get("tmdb_id"),
                    p.get("media_type"),
                    p.get("season"),
                    p.get("episode"),
                    min_sources=5,
                )
            else:
                # Always force_refresh on retry to bypass cached (possibly dead) URLs
                data = self.api_client.get_sources_api(
                    p.get("tmdb_id"), 
                    p.get("media_type"), 
                    p.get("season"), 
                    p.get("episode"),
                    force_refresh=True,
                )
            
            files = data.get("files", [])
            if files:
                wanted_quality = task.get("quality")
                files, mode = filter_sources_for_quality(files, wanted_quality)
                if mode == "unavailable_tagged":
                    self._log(
                        (
                            f"Requested quality '{wanted_quality}' not present in tagged refreshed sources; "
                            "strict mode keeps this task at requested quality only"
                        ),
                        level="WARNING",
                    )
                    return None
                if not files:
                    return None
                task["url"] = files[0].get("file")
                task["headers"] = files[0].get("headers")
                task["fallback_sources"] = files[1:] if len(files) > 1 else []
                self._log(f"Got {len(files)} fresh source(s) from API", level="INFO")
                return task["url"]
                
        except Exception as e:
            self._log(f"Link refresh failed: {e}", level="WARNING")
        
        return None

    def _build_source_list(self, task, refreshed_url):
        """Build prioritized list of sources to try."""
        sources = []
        
        # Primary URL
        primary = refreshed_url or task.get("url")
        if primary:
            sources.append({
                "file": primary, 
                "headers": task.get("headers"),
                "name": "primary"
            })
        
        # Fallback sources
        for i, fb in enumerate(task.get("fallback_sources", [])):
            if fb.get("file") and fb["file"] != primary:
                sources.append({
                    "file": fb["file"],
                    "headers": fb.get("headers"),
                    "name": f"fallback_{i+1}"
                })
        
        return sources

    def _try_download_source(self, source, output_path, task, _attempt_num, source_idx):  # NOSONAR
        """Attempt download from a single source with full error isolation."""
        url = self._extract_url(source.get("file"))
        if not url:
            return False

        if url != source.get("file"):
            self._log("Normalized source URL for download", level="INFO")

        self._log(
            f"Trying source {source_idx + 1} ({source.get('name')})...", 
            level="INFO"
        )

        # Keep partial file if it exists to allow resume
        if os.path.exists(output_path):
            try:
                size = os.path.getsize(output_path)
                if size == 0:
                    os.remove(output_path)
                else:
                    self._log(f"Keeping partial file for resume ({size} bytes)", level="INFO")
            except OSError as e:
                self._log(f"Could not inspect partial file: {e}", level="WARNING")

        try:
            # Determine download strategy
            is_worker = any(x in url for x in ["workers.dev", "storm", "vidrock"])
            
            # Try yt-dlp first
            success = self._download_with_ytdlp(url, output_path, task, source, is_worker)
            
            # Fallback to direct download only for genuine non-HLS URLs.
            # Check for .m3u8, m3u8, master, playlist, index in URL to catch
            # obfuscated HLS streams (e.g. CloudFront .txt endpoints).
            if not success:
                url_lower = url.lower()
                looks_like_hls = any(sig in url_lower for sig in [
                    ".m3u8", "m3u8", "master", "playlist", "/hls/", "/index",
                ])
                if not looks_like_hls:
                    success = self._direct_download(url, output_path, source.get("headers"), task)
            
            # Validate result - check the expected path or find yt-dlp's actual output
            # Get expected runtime from TMDB metadata (minutes)
            expected_runtime = None
            _meta = task.get("meta") or {}
            if isinstance(_meta, dict):
                expected_runtime = _meta.get("runtime")

            if success:
                if os.path.exists(output_path) and self._validate_download(output_path, expected_runtime):
                    return True
                # yt-dlp may have written to a different extension
                temp_dir = os.path.dirname(output_path)
                base_prefix = os.path.basename(output_path).split("_")[0]  # temp_id
                for fname in os.listdir(temp_dir):
                    if fname.startswith(base_prefix) and not fname.endswith(('.part', '.ytdl', '.temp')):
                        candidate = os.path.join(temp_dir, fname)
                        if candidate != output_path and os.path.isfile(candidate) and self._validate_download(candidate, expected_runtime):
                            # Rename to expected output_path
                            try:
                                shutil.move(candidate, output_path)
                                return True
                            except Exception:
                                return True  # file exists at candidate
                
                self._safe_remove(output_path)
                self._cleanup_ytdlp_temps(output_path)
                return False
            else:
                self._safe_remove(output_path)
                self._cleanup_ytdlp_temps(output_path)
                return False
                
        except Exception as e:
            self._log(f"Source {source_idx + 1} failed: {e}", level="ERROR")
            self._safe_remove(output_path)
            self._cleanup_ytdlp_temps(output_path)
            return False

    def _download_with_ytdlp(self, url, output_path, task, source, is_worker):  # NOSONAR
        """Execute yt-dlp with optimized parameters for faster, more reliable HLS downloads.

        All sources from providers are HLS (m3u8) streams — there is no direct-MP4
        alternative path from these CDNs.  The flags below are tuned specifically for HLS:

        KEY CHOICES
        -----------
        --hls-prefer-native REMOVED:
            Forces yt-dlp's Python HLS downloader instead of delegating to ffmpeg.
            The native downloader has worse fragment-error recovery and produces an
            intermediate .ts file that needs a full extra transcode pass.  Without
            this flag yt-dlp uses ffmpeg directly as the HLS fragment downloader,
            which avoids the extra mux step for well-formed streams.

        --hls-use-mpegts REMOVED:
            Merges all fragments into a single .ts container *while* downloading, then
            hands that .ts to ffmpeg for remux.  Any PTS/DTS discontinuity in a CDN
            ad-splice fragment is baked into the TS and propagates into the final mp4.
            Without it, yt-dlp+ffmpeg handles each fragment in isolation and then does
            a clean merge — the canonical path that avoids the "fragment mixing" issue.

        --http-chunk-size REMOVED:
            Applies only to plain HTTP downloads, not to HLS fragment requests.
            Setting it high had no effect on fragment streams and wasted memory.

        --retry-sleep changed 0.5 → "fragment:exp=1:20":
            Exponential back-off on fragment retries, caps at 20 s.  0.5 s flat retry on CDN failures
            hammers the server and triggers 429 rate-limiting, which compounds errors.

        --fragment-retries reduced 20 → 10:
            Combined with exponential back-off, 10 retries with growing delays give
            a CDN plenty of time to recover without waiting forever.  Total retry
            window is ~60s per fragment (1+2+4+8+16+20+20+20+20+20=131s worst case),
            which comfortably outlasts most CDN rate-limit windows.

        aria2c --async-dns=false REMOVED:
            Intended for Linux; on Windows it causes up to 5 s DNS stall per
            connection.  Removed to fix connection setup latency on Windows.

        --prefer-ffmpeg REMOVED:
            Deprecated in yt-dlp 2025+.  Generates a warning on every run.

        Downloader routing (--downloader "dash,m3u8:native" + "http,https:aria2c"):
            Uses yt-dlp protocol-specific overrides instead of URL-pattern detection.
            HLS/DASH streams ALWAYS use the native downloader (even when the CDN
            obfuscates the URL with a .txt or other extension).  Only plain HTTP
            file downloads are delegated to aria2c.  aria2c connections are capped
            at 16 (its hard limit).
        """
        ytdlp_exe = find_executable("yt-dlp")
        if not ytdlp_exe:
            self._log("yt-dlp not found in PATH, skipping", level="WARNING")
            return False

        # Fragment concurrency: workers (behind Cloudflare) get conservative concurrency
        # to avoid 429s; regular CDN sources get full parallelism.
        # If a previous source was rate-limited (429), halve concurrency to avoid
        # triggering the same CDN edge's rate limiter again.
        was_rate_limited = task.get("_got_rate_limited", False)
        if was_rate_limited:
            fragments = os.getenv("YTDLP_CONCURRENT_FRAGMENTS") or "4"
            self._log("Using reduced fragment concurrency (rate-limit detected earlier)", level="INFO")
        else:
            fragments = os.getenv("YTDLP_CONCURRENT_FRAGMENTS") or ("8" if is_worker else "16")

        output_path = os.path.abspath(output_path)
        output_dir = os.path.dirname(output_path)

        ffmpeg_bin = find_executable("ffmpeg") or "ffmpeg"

        cmd = [
            ytdlp_exe,
            url,
            "-o", output_path,
            "--paths", f"temp:{output_dir}",
            "--merge-output-format", "mp4",
            "--newline",
            "--no-warnings",
            "--no-check-certificates",
            "--fragment-retries", "10",           # 10 retries w/ exp backoff = ~60s window for CDN recovery
            "--retry-sleep", "fragment:exp=1:20",  # backoff cap 20s (1→2→4→8→16→20s); avoids hammering rate-limited CDNs
            "--extractor-retries", "3",           # retry manifest fetch on CDN hiccup
            "--concurrent-fragments", fragments,
            "--socket-timeout", "15",             # fail fast on dead connections
            "--retries", "5",                     # reduced: if it fails 5 times, try fallback source
            "--no-playlist",
            "--fixup", "never",
            "--ffmpeg-location", ffmpeg_bin,
            "--buffer-size", "16M",               # larger read-ahead = fewer stall pauses
            "--postprocessor-args",
            "ffmpeg:-movflags +faststart",
            "--no-write-description",
            "--no-write-info-json",
            "--no-write-thumbnail",
            "--continue",
        ]

        # Quality / format selection: when the user chose a specific resolution,
        # tell yt-dlp to pick the matching HLS variant from the m3u8 manifest.
        # For HLS sources the format selector targets video height; bestaudio covers
        # the separated audio playlist when the manifest uses DASH-style tracks.
        quality = task.get("quality")
        if quality and quality not in ("auto", "best"):
            q = quality.lower().replace("p", "").strip()
            height_map = {"4k": 2160, "2160": 2160, "1080": 1080, "720": 720, "480": 480, "360": 360, "240": 240}
            height = height_map.get(q)
            if height is None:
                try:
                    height = int(q)
                except ValueError:
                    height = None
            if height is not None:
                # Strict quality lock for downloads: no fallback to global best.
                fmt = f"bestvideo[height<={height}]+bestaudio/best[height<={height}]"
                cmd.extend(["--format", fmt])

        # Downloader routing: use protocol-specific overrides so yt-dlp always
        # uses its native downloader for HLS/DASH (even when the URL is obfuscated,
        # e.g. CloudFront .txt endpoints), and only delegates to aria2c for plain
        # HTTP/HTTPS file downloads.  This avoids the old bug where a .txt URL
        # serving HLS was misclassified, causing aria2c to crash on fragments.
        #
        # --downloader "dash,m3u8:native" forces native for manifest-based streams.
        # --downloader "http,https:aria2c" delegates only plain file downloads.
        cmd.extend(["--downloader", "dash,m3u8:native"])

        if is_tool_available("aria2c"):
            # aria2c max-connection-per-server is capped at 16 by aria2c itself.
            conn_env = os.getenv("ARIA2C_CONNECTIONS")
            if conn_env:
                conn = str(min(int(conn_env), 16))
            else:
                conn = "12" if is_worker else "16"
            cmd.extend([
                "--downloader", "http,https:aria2c",
                "--downloader-args",
                f"aria2c:-x {conn} -s {conn} -k 5M --file-allocation=none "
                f"--max-tries=10 --retry-wait=2 --timeout=30 "
                f"--connect-timeout=10 --split={conn}"
            ])

        # Speed throttle: --limit-rate caps yt-dlp bandwidth (user-configurable)
        speed_limit_mb = 0
        try:
            speed_limit_mb = float(task.get("speed_limit_mb") or 0)
        except (TypeError, ValueError):
            pass
        if speed_limit_mb > 0:
            # yt-dlp accepts bytes/s; convert MB/s → bytes/s
            cmd.extend(["--limit-rate", f"{int(speed_limit_mb * 1024 * 1024)}"])

        # Add headers
        headers = source.get("headers") or {}
        ua = headers.get("User-Agent") or headers.get("user-agent")
        if ua:
            cmd.extend(["--user-agent", ua])
        ref = headers.get("Referer") or headers.get("referer")
        if ref:
            cmd.extend(["--referer", ref])
        
        # Additional headers
        for k, v in headers.items():
            k_lower = k.lower()
            if k_lower not in ["user-agent", "referer"] and "," not in str(v):
                # Ensure header value is treated as a single string
                cmd.extend(["--add-header", f"{k}:{v}"])

        try:
            with self.lock:
                task["status_message"] = "Connecting..."
                self._save()

            log_event("download", f"yt-dlp cmd: {' '.join(cmd)}")

            # CREATE_NO_WINDOW prevents yt-dlp from stealing/interfering with
            # the console that the Rich Live display and prompt_toolkit use.
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
            
            # Non-blocking output reading using a queue
            output_queue = queue.Queue()
            recent_lines = deque(maxlen=20)
            def reader():
                try:
                    for line in iter(process.stdout.readline, ""):
                        output_queue.put(line)
                except Exception:
                    pass
                finally:
                    process.stdout.close()

            reader_thread = threading.Thread(target=reader, daemon=True)
            reader_thread.start()

            def _kill_proc():
                """Terminate yt-dlp, wait for it to die, and join reader thread."""
                try:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                except Exception:
                    pass
                reader_thread.join(timeout=3)

            last_progress_time = time.time()
            # Byte-level stall tracking.
            # last_bytes_time starts as None so the clock doesn't begin until
            # yt-dlp confirms actual download has started (Destination: line).
            # This prevents false-positives during the manifest-parse / CDN
            # negotiation phase which can easily exceed 45 s for 4K HLS streams.
            last_bytes_time    = None
            last_bytes_seen    = 0
            last_progress_pct  = 0          # Track progress % for stall detection
            download_started   = False   # True once yt-dlp writes 'Destination:'
            start_time = time.time()
            max_duration = 7200  # 2 hours max
            stall_timeout = 120  # 2 minutes without any output = stall
            bytes_stall_timeout = 90  # 90 s with zero byte progress after download starts
            muxing_started = False
            
            while True:
                # 1. Check overall timeout
                if time.time() - start_time > max_duration:
                    self._log("Download timeout exceeded", level="ERROR")
                    _kill_proc()
                    return False
                
                # 2. Check for stall (but be more lenient during muxing - 10 min)
                effective_stall_timeout = 600 if muxing_started else stall_timeout
                if time.time() - last_progress_time > effective_stall_timeout:
                    self._log(f"Download stalled (no output for {effective_stall_timeout//60} minutes)", level="ERROR")
                    with self.lock:
                        task["status_message"] = "STALLED"
                        self._save()
                    _kill_proc()
                    return False

                # 2b. Byte-level stall: only active once yt-dlp has confirmed it is
                #     writing to Destination (download_started=True).  Kills the
                #     process if no new bytes arrive within bytes_stall_timeout after
                #     the download has actually begun — handles the case where yt-dlp
                #     outputs progress lines but is stuck on a CDN fragment.
                #
                #     Uses BOTH byte count AND progress percentage as proof of activity:
                #     the native HLS downloader may update progress without updating
                #     byte counts if the output format doesn't match size_pair regex.
                if download_started and not muxing_started and last_bytes_time is not None:
                    cur_bytes = task.get("_bytes_downloaded", 0)
                    cur_progress = task.get("progress", 0)
                    # Reset stall clock if bytes OR progress advanced
                    if cur_bytes > last_bytes_seen or cur_progress > last_progress_pct:
                        last_bytes_seen = cur_bytes
                        last_progress_pct = cur_progress
                        last_bytes_time = time.time()
                    elif time.time() - last_bytes_time > bytes_stall_timeout:
                        self._log(
                            f"Byte-level stall: 0 bytes received in {bytes_stall_timeout}s "
                            f"after download started, trying next source", level="WARNING"
                        )
                        with self.lock:
                            task["status_message"] = "STALLED"
                            task["speed"] = "---"
                            self._save()
                        _kill_proc()
                        return False

                # 3. Check if process ended
                ret = process.poll()
                if ret is not None and output_queue.empty():
                    break
                
                # 4. Read from queue
                try:
                    while True:
                        line = output_queue.get_nowait()
                        if line:
                            stripped = line.strip()
                            recent_lines.append(stripped)
                            # Log important lines for debugging
                            if any(x in stripped.lower() for x in ["ffmpeg", "merger", "error", "100%", "complete"]):
                                self._log(f"yt-dlp: {stripped[:100]}", level="INFO")
                            # Detect CDN rate-limiting (429) — flag for adaptive concurrency
                            if "429" in stripped or "too many requests" in stripped.lower() or "rate limit" in stripped.lower():
                                with self.lock:
                                    task["_got_rate_limited"] = True
                                self._log("CDN rate-limiting detected (429)", level="WARNING")
                            # Detect muxing phase
                            if any(tag in stripped for tag in MUX_TAGS):
                                muxing_started = True
                                last_progress_time = time.time()  # Reset timer for mux
                                with self.lock:
                                    task["status"] = "muxing"
                                    task["status_message"] = "Muxing..."
                                    self._save()
                            if self._parse_progress_line(stripped, task):
                                last_progress_time = time.time()
                            # Start the byte-stall clock only once yt-dlp confirms
                            # it has opened the Destination file and is writing to it.
                            if not download_started and DESTINATION_TAG in stripped:
                                download_started = True
                                last_bytes_time  = time.time()
                                last_bytes_seen  = 0
                        output_queue.task_done()
                except queue.Empty:
                    pass
                
                if not self.running:
                    _kill_proc()
                    return False
                    
                time.sleep(0.1)
            
            # Drain any remaining lines from the output queue
            reader_thread.join(timeout=3)
            try:
                while True:
                    line = output_queue.get_nowait()
                    if line:
                        stripped = line.strip()
                        recent_lines.append(stripped)
                        self._parse_progress_line(stripped, task)
                    output_queue.task_done()
            except queue.Empty:
                pass

            if ret != 0:
                self._log(f"yt-dlp failed (code {ret}). Last output: {' | '.join(recent_lines)}", level="ERROR")
                log_event("download", f"yt-dlp failed (code {ret})", level="ERROR")
            return ret == 0
            
        except Exception as e:
            self._log(f"yt-dlp execution failed: {e}", level="ERROR")
            return False

    def _update_real_speed(self, task):
        """Calculate real download speed from byte samples (rolling window)."""
        samples = task.get("_speed_samples") or []
        now = time.time()
        current_bytes = task.get("_bytes_downloaded", 0)

        # Add current sample
        samples.append((now, current_bytes))

        # Keep only last 10 seconds of samples
        samples = [(t, b) for t, b in samples if now - t <= 10]
        task["_speed_samples"] = samples

        if len(samples) < 2:
            return

        oldest_t, oldest_b = samples[0]
        dt = now - oldest_t
        if dt < 0.5:
            return

        speed_bps = (current_bytes - oldest_b) / dt
        if speed_bps < 0:
            speed_bps = 0

        # Only override the yt-dlp-reported speed when we have real
        # byte-level data; otherwise keep the parsed "at X KiB/s" value.
        if speed_bps > 0:
            task["speed"] = self._bytes_to_human(int(speed_bps)) + "/s"
            task["_speed_bps"] = speed_bps

        # ETA from real speed
        total = task.get("_bytes_total", 0)
        if total > 0 and speed_bps > 0:
            remaining = total - current_bytes
            if remaining > 0:
                task["eta"] = self._format_time(remaining / speed_bps)
            else:
                task["eta"] = "done"
        elif speed_bps > 0 and task.get("progress", 0) > 0:
            # Estimate from progress percentage
            pct = task["progress"]
            elapsed = now - task.get("_dl_start_time", now)
            if pct > 0 and elapsed > 0:
                total_est = elapsed * 100 / pct
                remaining_est = total_est - elapsed
                task["eta"] = self._format_time(max(0, remaining_est))

    def _update_display_fields(self, task):
        """Update the human-readable downloaded/total_size fields from byte counters."""
        dl = task.get("_bytes_downloaded", 0)
        total = task.get("_bytes_total", 0)
        if total > 0:
            task["downloaded"] = self._bytes_to_human(dl)
            task["total_size"] = self._bytes_to_human(total)
        elif dl > 0:
            task["downloaded"] = self._bytes_to_human(dl)

    def _parse_progress_line(self, line, task):  # NOSONAR
        """Thread-safe progress parsing with real byte tracking. Returns True if progress was updated."""
        if not any(x in line for x in ["[download]", "[aria2c]", "[#", "[Merger]", "[ffmpeg]", "Merging", "Destination:", "[ExtractAudio]", "[FixupM3u8]", "has already been downloaded"]):
            return False

        updated = False
        try:
            with self.lock:
                # Initialize tracking fields if missing
                if "_frag_current" not in task:
                    task["_frag_current"] = 0
                    task["_frag_total"] = 1
                    task["_base_progress"] = 0

                # ── Fragment info ──
                frag_match = re.search(r"[Ff]ragment\s+(\d+)\s+of\s+(\d+)", line)
                if frag_match:
                    task["_frag_current"] = int(frag_match.group(1))
                    task["_frag_total"] = int(frag_match.group(2))
                    task["_base_progress"] = ((task["_frag_current"] - 1) / task["_frag_total"]) * 100
                    updated = True

                # ── Percentage ──
                pct_match = re.search(r"(\d+(?:\.\d+)?)%", line)
                if pct_match:
                    frag_pct = float(pct_match.group(1))
                    frag_total = task.get("_frag_total", 1)
                    if frag_total > 1:
                        overall = task.get("_base_progress", 0) + (frag_pct / frag_total)
                        if overall >= task.get("progress", 0):
                            task["progress"] = min(overall, 100)
                            updated = True
                    else:
                        if frag_pct >= task.get("progress", 0):
                            task["progress"] = frag_pct
                            updated = True

                # ── Byte sizes: "15.2MiB/25.4MiB" ──
                size_pair = re.search(r"([\d.]+\s*[KMG]?i?B)\s*/\s*([\d.]+\s*[KMG]?i?B)", line, re.IGNORECASE)
                if size_pair:
                    dl_bytes = self._parse_size_to_bytes(size_pair.group(1))
                    total_bytes = self._parse_size_to_bytes(size_pair.group(2))
                    if dl_bytes > 0:
                        task["_bytes_downloaded"] = dl_bytes
                    if total_bytes > 0:
                        task["_bytes_total"] = total_bytes
                    updated = True
                else:
                    # "of ~10MiB" for total — but yt-dlp also prints per-fragment
                    # sizes ("of ~1.2MiB") that are much smaller than the overall
                    # total.  Only accept the value if it's larger than what we
                    # already know, to avoid overwriting with a fragment size.
                    # The native HLS downloader may have spaces: "of ~  521.00MiB"
                    total_match = re.search(r"of\s+~?\s*([\d.]+\s*[KMG]?i?B)", line, re.IGNORECASE)
                    if total_match:
                        tb = self._parse_size_to_bytes(total_match.group(1))
                        if tb > 0 and tb > task.get("_bytes_total", 0):
                            task["_bytes_total"] = tb
                            # Estimate downloaded from progress
                            pct = task.get("progress", 0)
                            if pct > 0:
                                task["_bytes_downloaded"] = int((pct / 100) * tb)
                        updated = True

                # ── Speed from yt-dlp (used until we have enough byte samples) ──
                # Native HLS output format: "at    1.20MiB/s" (with variable spacing)
                speed_match = re.search(r"at\s+([\d.]+\s*[KMG]?i?B/s)", line, re.IGNORECASE)
                if not speed_match:
                    speed_match = re.search(r"\s([\d.]+\s*[KMG]?i?B/s)", line, re.IGNORECASE)
                if speed_match:
                    raw_speed = speed_match.group(1).replace(" ", "")
                    # Parse to bytes/s for real tracking
                    spd_bps = self._parse_size_to_bytes(raw_speed.replace("/s", ""))
                    if spd_bps > 0:
                        task["_speed_bps"] = spd_bps
                        task["speed"] = raw_speed
                    updated = True

                # ── Estimate _bytes_downloaded from progress when total is known ──
                # The native HLS downloader outputs "X% of ~Y" on each line;
                # size_pair ("X/Y") never matches, so _bytes_downloaded would
                # stay at 0 forever.  Always re-derive from progress × total.
                _total = task.get("_bytes_total", 0)
                _pct   = task.get("progress", 0)
                if _total > 0 and _pct > 0:
                    estimated = int((_pct / 100) * _total)
                    if estimated > task.get("_bytes_downloaded", 0):
                        task["_bytes_downloaded"] = estimated

                # ── Compute real speed & ETA from byte samples ──
                if updated:
                    self._update_real_speed(task)
                    self._update_display_fields(task)

                # ── Muxing phase ──
                if any(tag in line for tag in MUX_TAGS) or "Merging" in line:
                    task["status_message"] = "Muxing..."
                    task["status"] = "muxing"
                    if task.get("progress", 0) < 99:
                        task["progress"] = 99
                    task["speed"] = "---"
                    task["eta"] = "muxing"
                    self._save(force=True)
                    return True

                # ── Destination (download starting) ──
                if DESTINATION_TAG in line:
                    if task.get("progress", 0) == 0:
                        task["status_message"] = "Starting..."
                        task["_dl_start_time"] = time.time()
                    updated = True

                # ── Already downloaded ──
                if "has already been downloaded" in line:
                    task["progress"] = 100
                    task["speed"] = "---"
                    task["eta"] = "done"
                    updated = True

                # ── Near-complete ──
                if task.get("progress", 0) >= 99.5 and task.get("status") == "downloading":
                    task["status_message"] = "Finalizing..."
                    task["speed"] = "finalizing"
                    task["eta"] = "soon"

                task["_last_update"] = time.time()

                if updated and task.get("progress", 0) > 0 and task.get("progress", 0) < 99:
                    if task.get("status") != "muxing":
                        task["status_message"] = ""

                self._save()
            return updated

        except Exception:
            return False

    def _get_media_duration(self, file_path):
        """Get media duration in seconds using ffprobe. Returns 0 on failure."""
        ffprobe = find_executable("ffprobe")
        if not ffprobe:
            return 0
        try:
            _kw = {}
            if sys.platform == "win32":
                _kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            result = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", file_path],
                capture_output=True, text=True, timeout=30, **_kw,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except Exception as e:
            self._log(f"ffprobe duration check failed: {e}", level="WARNING")
        return 0

    def _validate_download(self, file_path, expected_runtime_min=None):  # NOSONAR
        """Validate that downloaded file is valid media.
        
        Args:
            file_path: Path to the downloaded file.
            expected_runtime_min: Expected runtime in minutes (from TMDB).
                If provided and ffprobe is available, rejects files whose
                actual duration is less than 80% of the expected runtime.
        """
        if not os.path.exists(file_path):
            return False
            
        size = os.path.getsize(file_path)
        
        # A complete TV episode or movie is always larger than 20 MB.
        # Partial files from killed yt-dlp processes are typically 1-10 MB,
        # so this threshold reliably rejects them without false positives.
        if size < 20 * 1024 * 1024:
            self._log(f"Validation failed: File too small ({size / (1024*1024):.1f} MB) — likely partial", level="WARNING")
            return False
            
        # Check if file starts with common media signatures
        try:
            with open(file_path, 'rb') as f:
                header = f.read(2048) # Read a larger chunk to check for HTML tags
                
            # Check for HTML signature (indicates error page)
            header_str = header.lower()
            if b'<!doctype html' in header_str or b'<html' in header_str or b'<head' in header_str:
                self._log("Validation failed: File is HTML, not video", level="ERROR")
                return False
                
            # Check for obvious video signatures (basic check)
            # ftyp (mp4), \x30\x26\xB2\x75 (wmv), \x1A\x45\xDF\xA3 (mkv)
            video_signatures = [b'ftyp', b'\x30\x26\xB2\x75', b'\x1A\x45\xDF\xA3', b'OggS', b'RIFF']
            found_sig = any(sig in header for sig in video_signatures)
            
            if not found_sig and size < 5 * 1024 * 1024:
                # If no signature and small, be suspicious
                self._log("Validation Warning: No common video signature found in small file", level="WARNING")
                
        except Exception as e:
            self._log(f"Validation error: {e}", level="WARNING")
            # If we can't validate but file is big, assume OK
            if size < 20 * 1024 * 1024:
                return False

        # ── Duration check via ffprobe ──
        # If we know how long the episode/movie should be (from TMDB),
        # reject files where the actual duration < 80% of expected.
        # This catches truncated HLS downloads that pass the size check
        # (e.g. 580 MB but only 30 of 43 minutes).
        if expected_runtime_min and expected_runtime_min > 0:
            actual_secs = self._get_media_duration(file_path)
            if actual_secs > 0:
                expected_secs = expected_runtime_min * 60
                ratio = actual_secs / expected_secs
                # Use a more lenient threshold for TV episodes because
                # TMDB runtimes can be inaccurate (e.g. 43 min listed but
                # actual episode is only 38 min).  0.60 for TV, 0.80 for movies.
                min_ratio = 0.60 if expected_runtime_min < 90 else 0.80
                if ratio < min_ratio:
                    self._log(
                        f"Validation failed: Duration too short "
                        f"({actual_secs/60:.1f} min vs expected {expected_runtime_min} min, "
                        f"{ratio*100:.0f}%) — likely truncated stream",
                        level="WARNING"
                    )
                    return False
                self._log(
                    f"Duration OK: {actual_secs/60:.1f} min / {expected_runtime_min} min ({ratio*100:.0f}%)",
                    level="INFO"
                )

        return True

    def _parse_size_to_bytes(self, size_str):
        """Convert size string like '15.2MiB' to bytes."""
        try:
            # Remove any ~ prefix
            size_str = size_str.replace('~', '').strip()
            
            # Parse number and unit
            match = re.match(r'([\d.]+)([KMG]?i?B?)', size_str, re.IGNORECASE)
            if not match:
                return 0
                
            num = float(match.group(1))
            unit = match.group(2).upper()
            
            # Convert to bytes
            multipliers = {
                'B': 1, '': 1,
                'KB': 1000, 'KIB': 1024, 'K': 1024,
                'MB': 1000000, 'MIB': 1024 * 1024, 'M': 1024 * 1024,
                'GB': 1000000000, 'GIB': 1024 * 1024 * 1024, 'G': 1024 * 1024 * 1024
            }
            
            return int(num * multipliers.get(unit, 1))
        except (TypeError, ValueError):
            return 0
    
    def _bytes_to_human(self, bytes_val):
        """Convert bytes to human readable string."""
        try:
            bytes_val = int(bytes_val)
            if bytes_val < 1024:
                return f"{bytes_val}B"
            elif bytes_val < 1024 * 1024:
                return f"{bytes_val / 1024:.1f}KB"
            elif bytes_val < 1024 * 1024 * 1024:
                return f"{bytes_val / (1024 * 1024):.1f}MB"
            else:
                return f"{bytes_val / (1024 * 1024 * 1024):.1f}GB"
        except (TypeError, ValueError):
            return "Unknown"
    
    def _format_time(self, seconds):
        """Format seconds into readable time string."""
        try:
            seconds = int(seconds)
            if seconds < 60:
                return f"{seconds}s"
            elif seconds < 3600:
                return f"{seconds // 60}m{seconds % 60:02d}s"
            else:
                hours = seconds // 3600
                minutes = (seconds % 3600) // 60
                return f"{hours}h{minutes:02d}m"
        except (TypeError, ValueError):
            return "---"

    def _direct_download(self, url, output_path, headers, task):  # NOSONAR
        """Robust direct download with range request support."""
        download_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Connection": "keep-alive"
        }
        if headers:
            download_headers.update(headers)

        try:
            session = self._build_session()
            # Probe server capabilities (HEAD can be blocked; fall back to GET Range)
            head_resp = None
            try:
                head_resp = session.head(
                    url,
                    headers=download_headers,
                    timeout=10,
                    allow_redirects=True,
                    verify=False,
                )
            except Exception:
                pass

            if head_resp is None or head_resp.status_code >= 400:
                probe_headers = download_headers.copy()
                probe_headers["Range"] = "bytes=0-1024"
                head_resp = session.get(
                    url,
                    headers=probe_headers,
                    timeout=10,
                    allow_redirects=True,
                    stream=True,
                    verify=False,
                )

            # Close streaming response body to release the connection back to
            # the pool.  We only needed the headers for the probe.
            try:
                head_resp.close()
            except Exception:
                pass

            content_type = head_resp.headers.get('content-type', '').lower()
            total_size = int(head_resp.headers.get('content-length', 0))
            accept_ranges = head_resp.headers.get('accept-ranges', '').lower() == 'bytes'
            
            # Reject obvious error pages
            if 'text/html' in content_type and total_size < 1024 * 1024:
                self._log(f"Rejected HTML response ({total_size} bytes)", level="ERROR")
                return False
            
            # Choose strategy based on server capabilities
            if accept_ranges and total_size > 0:
                existing = 0
                if os.path.exists(output_path):
                    try:
                        existing = os.path.getsize(output_path)
                    except OSError:
                        existing = 0

                if 0 < existing < total_size:
                    return self._single_threaded_download(
                        url, output_path, download_headers, task, total_size, resume_from=existing
                    )

                return self._parallel_range_download(
                    url, output_path, download_headers, total_size, task
                )
            else:
                return self._single_threaded_download(
                    url, output_path, download_headers, task, total_size
                )
                
        except Exception as e:
            self._log(f"Direct download failed: {e}", level="ERROR")
            self._safe_remove(output_path)
            return False

    def _parallel_range_download(self, url, output_path, headers, total_size, task):  # NOSONAR
        """Download using multiple connections with proper error handling."""
        # 1 thread per 5 MB, min 4, max 16; tiny files fall back to single-threaded
        num_threads = min(16, max(4, total_size // (5 * 1024 * 1024)))
        if num_threads < 2 or total_size < 2 * 1024 * 1024:
            return self._single_threaded_download(url, output_path, headers, task, total_size)
        
        chunk_size = total_size // num_threads
        
        # Pre-allocate file
        try:
            with open(output_path, 'wb') as f:
                f.seek(total_size - 1)
                f.write(b'\0')
        except OSError as e:
            self._log(f"Cannot pre-allocate file: {e}", level="ERROR")
            return False
        
        progress = {"downloaded": 0, "last_update": time.time()}
        progress_lock = threading.Lock()
        errors = []
        error_lock = threading.Lock()

        def download_chunk(start, end, chunk_id):
            """Download a specific byte range."""
            chunk_headers = headers.copy()
            chunk_headers["Range"] = f"bytes={start}-{end}"
            
            try:
                session = self._build_session()
                with session.get(
                    url,
                    headers=chunk_headers,
                    stream=True,
                    timeout=60,
                    verify=False,
                ) as resp:
                    resp.raise_for_status()
                    
                    with open(output_path, "r+b") as f:
                        f.seek(start)
                        for chunk in resp.iter_content(chunk_size=1024 * 1024):  # 1 MB chunks
                            if not self.running:
                                return False
                            if chunk:
                                f.write(chunk)
                                with progress_lock:
                                    progress["downloaded"] += len(chunk)
                                    
                                    # Update progress every second
                                    now = time.time()
                                    if now - progress["last_update"] > 0.5:
                                        pct = (progress["downloaded"] / total_size) * 100
                                        with self.lock:
                                            task["progress"] = pct
                                            task["_bytes_downloaded"] = progress["downloaded"]
                                            task["_bytes_total"] = total_size
                                            self._update_real_speed(task)
                                            self._update_display_fields(task)
                return True
            except Exception as e:
                with error_lock:
                    errors.append(f"Chunk {chunk_id}: {e}")
                return False

        # Execute parallel download
        try:
            with ThreadPoolExecutor(max_workers=num_threads) as executor:
                futures = []
                for i in range(num_threads):
                    start = i * chunk_size
                    end = (i + 1) * chunk_size - 1 if i < num_threads - 1 else total_size - 1
                    futures.append(executor.submit(download_chunk, start, end, i))
                
                # Wait for all with timeout
                results = []
                for future in as_completed(futures):
                    results.append(future.result(timeout=300))

            if all(results) and not errors:
                with self.lock:
                    task["progress"] = 100
                return True
            else:
                self._log(f"Parallel download had errors: {errors}", level="ERROR")
                return False
                
        except Exception as e:
            self._log(f"Parallel download failed: {e}", level="ERROR")
            return False

    def _single_threaded_download(self, url, output_path, headers, task, expected_size=0, resume_from=0):  # NOSONAR
        """Reliable single-threaded fallback download with retry on connection reset."""
        max_retries = 3
        for dl_attempt in range(max_retries):
            try:
                dl_headers = dict(headers) if headers else {}
                current_resume = resume_from
                if current_resume > 0:
                    dl_headers["Range"] = f"bytes={current_resume}-"

                session = self._build_session()
                with session.get(
                    url,
                    headers=dl_headers,
                    stream=True,
                    timeout=60,
                    verify=False,
                ) as resp:
                    
                    if resp.status_code not in [200, 206]:
                        self._log(f"HTTP {resp.status_code}", level="ERROR")
                        return False
                    
                    # Check content type
                    content_type = resp.headers.get('content-type', '').lower()
                    if 'text/html' in content_type:
                        self._log("Got HTML instead of video", level="ERROR")
                        return False
                    
                    total = int(resp.headers.get('content-length', expected_size))
                    if current_resume > 0 and resp.status_code == 206:
                        total = current_resume + total
                    downloaded = current_resume if current_resume > 0 else 0
                    last_update = time.time()
                    
                    mode = 'ab' if current_resume > 0 else 'wb'
                    with open(output_path, mode) as f:
                        for chunk in resp.iter_content(chunk_size=8 * 1024 * 1024):  # 8 MB → fewer syscalls
                            if not self.running:
                                return False
                            if chunk:
                                f.write(chunk)
                                downloaded += len(chunk)
                                
                                now = time.time()
                                if now - last_update > 0.5:
                                    with self.lock:
                                        if total > 0:
                                            task["progress"] = (downloaded / total) * 100
                                        task["_bytes_downloaded"] = downloaded
                                        task["_bytes_total"] = total if total > 0 else 0
                                        self._update_real_speed(task)
                                        self._update_display_fields(task)
                                        self._save()
                                    last_update = now
                
                return True
                
            except (requests.exceptions.ConnectionError,
                    requests.exceptions.ChunkedEncodingError,
                    OSError) as e:
                if dl_attempt < max_retries - 1:
                    # Resume from where we left off for retriable errors
                    if os.path.exists(output_path):
                        try:
                            resume_from = os.path.getsize(output_path)
                        except OSError:
                            resume_from = 0
                    wait = 2 ** dl_attempt
                    self._log(f"Connection reset, retrying in {wait}s (attempt {dl_attempt + 2}/{max_retries})...", level="WARNING")
                    time.sleep(wait)
                    continue
                self._log(f"Single-threaded download error after {max_retries} attempts: {e}", level="ERROR")
                return False
            except Exception as e:
                if "timeout" in str(e).lower():
                    self._log("Download timeout", level="ERROR")
                    return False
                self._log(f"Single-threaded download error: {e}", level="ERROR")
                return False
        return False

    def _safe_remove(self, path):
        """Safely remove a file with retries."""
        if not path or not os.path.exists(path):
            return
            
        for attempt in range(3):
            try:
                os.remove(path)
                return
            except OSError:
                time.sleep(0.5 * (attempt + 1))
        # Give up after 3 tries

    def _cleanup_ytdlp_temps(self, output_path):
        """Remove yt-dlp temporary/partial files (.part, .ytdl, .temp) for a given output path."""
        if not output_path:
            return
        try:
            directory = os.path.dirname(output_path)
            base = os.path.basename(output_path)
            prefix = base.split("_")[0]  # temp_id
            for fname in os.listdir(directory):
                if fname.startswith(prefix) and fname.endswith(('.part', '.ytdl', '.temp')):
                    try:
                        os.remove(os.path.join(directory, fname))
                    except OSError:
                        pass
        except Exception as e:
            app_logger.debug(f"Suppressed error in download_manager: {e}", exc_info=True)

    def _finalize_task(self, task, success, temp_path, error_msg):  # NOSONAR
        """Clean up task state and trigger post-processing."""
        with self.lock:
            # Clean up internal tracking fields (not needed in saved state)
            for key in ["_frag_current", "_frag_total", "_base_progress"]:
                task.pop(key, None)
            
            if success:
                task["status"] = "muxing"
                task["progress"] = 100
                task["speed"] = "-"
                task["eta"] = "-"
            else:
                task["status"] = "error"
                task["error_log"] = error_msg or "Unknown error"
            self._save(force=True)
        
        if success:
            try:
                self._organize_download(task, temp_path)
                self._embed_subtitles(task)
                self._notify_completion(task["title"], success=True)
                # Record provider success so it rises in future rankings
                _provider = (task.get("meta") or {}).get("provider") or ""
                if _provider:
                    try:
                        from src.utils.validator import report_source_result
                        report_source_result(_provider, True)
                    except Exception:
                        pass
            except Exception as e:
                self._log(f"Post-processing failed: {e}", level="ERROR")
                with self.lock:
                    task["status"] = "error"
                    task["error_log"] = f"Post-processing: {str(e)}"
                    self._save(force=True)
        else:
            # Record provider failure
            _provider = (task.get("meta") or {}).get("provider") or ""
            if _provider:
                try:
                    from src.utils.validator import report_source_result
                    report_source_result(_provider, False)
                except Exception:
                    pass

    def _notify_completion(self, title: str, success: bool = True):  # NOSONAR
        """Send a desktop notification when a download finishes."""
        msg   = f"\u2705 Download complete: {title}" if success else f"\u274c Download failed: {title}"
        icon  = "cinema-cli"
        def _fire():
            try:
                if sys.platform == "win32":
                    # Try winotify first, then plyer, then toast fallbacks
                    try:
                        from winotify import Notification, audio  # type: ignore
                        toast = Notification(
                            app_id=APP_NAME,
                            title=APP_NAME,
                            msg=msg,
                            duration="short",
                        )
                        toast.show()
                        return
                    except ImportError:
                        pass
                    try:
                        from plyer import notification as _plyer  # type: ignore
                        _plyer.notify(title=APP_NAME, message=msg, timeout=6)
                        return
                    except ImportError:
                        pass
                    # Last resort: PowerShell toast (no extra deps)
                    ps_cmd = (
                        f'[Windows.UI.Notifications.ToastNotificationManager, '
                        f'Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;'
                        f'$t="""<toast><visual><binding template=\"ToastText01\">'
                        f'<text id=\"1\">{msg}</text></binding></visual></toast>""";'
                        f'$x=[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, '
                        f'ContentType=WindowsRuntime]::New();$x.LoadXml($t);'
                        f'$n=[Windows.UI.Notifications.ToastNotification]::New($x);'
                        f'[Windows.UI.Notifications.ToastNotificationManager]'
                        f'::CreateToastNotifier("{APP_NAME}").Show($n)'
                    )
                    _kw = {}
                    if sys.platform == "win32":
                        _kw["creationflags"] = subprocess.CREATE_NO_WINDOW
                    subprocess.run(
                        ["powershell", "-WindowStyle", "Hidden", "-Command", ps_cmd],
                        capture_output=True, timeout=5, **_kw,
                    )
                elif sys.platform == "darwin":
                    subprocess.run(
                        ["osascript", "-e",
                         f'display notification "{msg}" with title "{APP_NAME}"'],
                        capture_output=True, timeout=5,
                    )
                else:
                    subprocess.run(
                        ["notify-send", "-a", APP_NAME, "-i", icon, APP_NAME, msg],
                        capture_output=True, timeout=5,
                    )
            except Exception:
                pass  # Notifications are best-effort — never crash the downloader
        threading.Thread(target=_fire, daemon=True).start()

    def get_queue(self):
        def _status_rank(status):
            if status in ("downloading", "muxing"):
                return 0
            if status == "pending":
                return 1
            if status == "error":
                return 2
            return 3

        with self.lock:
            return sorted(self.queue, key=lambda x: (_status_rank(x["status"]), x.get("added_at", 0)))

    def retry_task(self, task_id):
        with self.lock:
            for task in self.queue:
                if task["id"] == task_id:
                    task["status"] = "pending"
                    task["progress"] = 0
                    task["speed"] = "0 B/s"
                    task["eta"] = "00:00"
                    task["retries"] = 0
                    task["error_log"] = "Manual retry triggered.\n"
                    # Clear fragment tracking
                    for key in ["_frag_current", "_frag_total", "_base_progress"]:
                        task.pop(key, None)
                    self._save()
                    return True
        return False

    def remove_task(self, task_id):
        with self.lock:
            # If removing the currently active download, clear the active slot
            # so the listener immediately picks up the next pending task.
            if self._current_task_id == task_id:
                self._current_task_id = None
                # Cancel the running future if possible
                fut = self.active_tasks.pop(task_id, None)
                if fut is not None:
                    fut.cancel()
            self.queue = [t for t in self.queue if t["id"] != task_id]
            self._save()
            return True

    def clear_completed(self):
        with self.lock:
            self.queue = [t for t in self.queue if t["status"] != "completed"]
            self._save()
            return True

    
    def _organize_download(self, task, temp_file_path):  # NOSONAR
        """Move downloaded file (and any downloaded subtitles) into the user's downloads folder."""
        if not temp_file_path or not os.path.exists(temp_file_path):
            # nothing to move
            return

        meta = task.get("meta") or {}
        title = task.get("title") or ""
        downloads_root = self.downloads_dir

        media_type = meta.get("type", "movie")
        if media_type == "tv":
            series = title.split(" S")[0]
            season = meta.get("season", 1)
            dest_dir = os.path.join(
                downloads_root,
                "tv",
                sanitize_filename(series),
                f"Season {int(season):02d}",
            )
        else:
            dest_dir = os.path.join(downloads_root, "movies", sanitize_filename(title))

        os.makedirs(dest_dir, exist_ok=True)

        # If the task filename is just a name, keep that base name; otherwise use the temp file basename
        desired_name = os.path.basename(task.get("filename") or "") or os.path.basename(temp_file_path)
        # Ensure extension matches the downloaded file
        _, dl_ext = os.path.splitext(temp_file_path)
        base, ext = os.path.splitext(desired_name)
        if not ext:
            desired_name = base + dl_ext

        dest_path = os.path.join(dest_dir, desired_name)

        # Avoid overwriting existing files
        if os.path.exists(dest_path):
            base, ext = os.path.splitext(dest_path)
            dest_path = f"{base}.{int(time.time())}{ext}"

        try:
            shutil.move(temp_file_path, dest_path)
        except Exception as e:
            self._log(f"Failed to move downloaded file: {e}", level="ERROR")
            return

        # Move any downloaded subtitle files alongside
        moved_subs = []
        try:
            subs = task.get("subtitle_files") or []
            for sub in subs:
                sub_path = sub.get("path")
                if not sub_path or not os.path.exists(sub_path):
                    continue
                sub_dest = os.path.join(os.path.dirname(dest_path), os.path.basename(sub_path))
                try:
                    shutil.move(sub_path, sub_dest)
                    sub["path"] = sub_dest
                    moved_subs.append(sub)
                except Exception as e:
                    self._log(f"Failed to move subtitle {sub_path}: {e}", level="WARNING")
        except Exception as e:
            app_logger.debug(f"Suppressed error in download_manager: {e}", exc_info=True)

        with self.lock:
            task["filename"] = dest_path
            if moved_subs:
                task["subtitle_files"] = moved_subs
                task["subtitle_file"] = moved_subs[0]["path"]
            self._save()

    
    # ISO 639-2/T three-letter codes — MP4 containers (mdhd atom) require
    # three-letter language codes.  Two-letter codes silently fail to persist
    # in some ffmpeg builds, resulting in tracks with no language metadata.
    _LANG_TO_ISO639_2 = {
        "ar": "ara", "en": "eng", "fr": "fra", "es": "spa",
        "de": "deu", "tr": "tur", "pt": "por", "it": "ita",
        "zh": "zho", "ja": "jpn", "ko": "kor", "hi": "hin",
        "ru": "rus", "nl": "nld", "pl": "pol", "sv": "swe",
        "no": "nor", "da": "dan", "fi": "fin", "el": "ell",
        "he": "heb", "ro": "ron", "hu": "hun", "cs": "ces",
        "th": "tha", "vi": "vie", "id": "ind", "ms": "msa",
        "uk": "ukr", "bg": "bul", "hr": "hrv", "sr": "srp",
        "sk": "slk", "sl": "slv", "et": "est", "lv": "lav",
        "lt": "lit", "fa": "fas", "ur": "urd", "bn": "ben",
        "ta": "tam", "te": "tel", "ml": "mal", "sw": "swa",
    }

    @classmethod
    def _to_iso639_2(cls, code: str) -> str:
        """Convert a 2-letter ISO 639-1 code to 3-letter ISO 639-2/T.
        Returns the input unchanged if already 3+ letters or not found."""
        c = (code or "und").strip().lower()
        return cls._LANG_TO_ISO639_2.get(c, c)

    def _sync_subtitles_to_video(self, video_path, subs):  # NOSONAR
        """Auto-synchronise subtitle files to the video's audio track.

        Uses ``ffsubsync`` (audio-based alignment) to correct timing
        differences between community subtitles (typically timed for a
        retail/Blu-ray release) and the actual CDN HLS stream, which may
        have different start-points, recaps, or edits.

        For each subtitle file the method:
          1. Runs ``ffsubsync <video> -i <srt> -o <synced_srt>``
          2. If successful, replaces the sub's ``path`` in-place with the
             synced version.
          3. If ffsubsync is unavailable or fails, the original (unsynced)
             subtitle is kept — this is a *best-effort* enhancement, never a
             hard requirement.

        Parameters
        ----------
        video_path : str
            Absolute path to the video file (MP4/MKV).
        subs : list[dict]
            Subtitle dicts with at least ``path`` (and optionally ``lang``).
            Modified **in-place**: ``path`` is updated to the synced file on
            success.
        """
        if not subs or not video_path or not os.path.exists(video_path):
            return

        # Locate the ffsubsync executable.
        # Priority: 1) same venv as the running interpreter  2) PATH
        ffsubsync_exe = None
        venv_candidate = os.path.join(
            os.path.dirname(sys.executable), "ffsubsync.exe" if sys.platform == "win32" else "ffsubsync"
        )
        if os.path.isfile(venv_candidate):
            ffsubsync_exe = venv_candidate
        elif shutil.which("ffsubsync"):
            ffsubsync_exe = shutil.which("ffsubsync")
        else:
            # Try running as a module
            try:
                probe = subprocess.run(
                    [sys.executable, "-c", "import ffsubsync"],
                    capture_output=True, timeout=10,
                )
                if probe.returncode == 0:
                    ffsubsync_exe = "__module__"  # sentinel: invoke via python -m
            except Exception:
                pass

        if ffsubsync_exe is None:
            self._log("ffsubsync not available — skipping subtitle sync", level="INFO")
            return

        self._log(f"Syncing {len(subs)} subtitle(s) to video audio...", level="INFO")

        _kw = {}
        if sys.platform == "win32":
            _kw["creationflags"] = subprocess.CREATE_NO_WINDOW

        for sub in subs:
            sub_path = sub.get("path")
            if not sub_path or not os.path.exists(sub_path):
                continue

            lang = sub.get("lang") or "?"
            synced_path = sub_path.rsplit(".", 1)[0] + ".synced.srt"

            try:
                if ffsubsync_exe == "__module__":
                    cmd = [
                        sys.executable, "-m", "ffsubsync",
                        video_path,
                        "-i", sub_path,
                        "-o", synced_path,
                    ]
                else:
                    cmd = [
                        ffsubsync_exe,
                        video_path,
                        "-i", sub_path,
                        "-o", synced_path,
                    ]

                # ffsubsync typically takes 10–60 s per file (audio extraction
                # + speech-activity detection + alignment).
                # A 90 s timeout per subtitle is generous.
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="ignore",
                    timeout=90,
                    **_kw,
                )

                if os.path.exists(synced_path) and os.path.getsize(synced_path) > 10:
                    # Parse offset from ffsubsync stdout/stderr for logging
                    combined = (result.stdout or "") + (result.stderr or "")
                    offset_match = re.search(r"offset seconds:\s*([-\d.]+)", combined)
                    offset_str = offset_match.group(1) if offset_match else "?"
                    self._log(
                        f"  [{lang}] synced (offset: {offset_str}s)",
                        level="INFO",
                    )
                    # Replace the original with the synced version
                    try:
                        os.remove(sub_path)
                    except OSError:
                        pass
                    # Rename synced file to the original name so downstream
                    # code (embed, external playback) doesn't need changes.
                    try:
                        shutil.move(synced_path, sub_path)
                    except OSError:
                        # If rename fails, update the path reference instead
                        sub["path"] = synced_path
                else:
                    self._log(
                        f"  [{lang}] ffsubsync produced no output — keeping original",
                        level="WARNING",
                    )
                    # Clean up empty/missing synced file
                    if os.path.exists(synced_path):
                        try:
                            os.remove(synced_path)
                        except OSError:
                            pass

            except subprocess.TimeoutExpired:
                self._log(f"  [{lang}] ffsubsync timed out (90s) — keeping original", level="WARNING")
                if os.path.exists(synced_path):
                    try:
                        os.remove(synced_path)
                    except OSError:
                        pass
            except Exception as e:
                self._log(f"  [{lang}] ffsubsync error: {e} — keeping original", level="WARNING")
                if os.path.exists(synced_path):
                    try:
                        os.remove(synced_path)
                    except OSError:
                        pass

        self._log("Subtitle sync pass complete", level="INFO")

    def _embed_subtitles(self, task):  # NOSONAR
        """Embed subtitle tracks into the final media file.

        This is a **pure copy-remux**: video and audio are stream-copied, only
        subtitle streams are transcoded (SRT/VTT → mov_text for MP4, or kept as
        SRT for MKV).  No frame/sample manipulation is performed.

        CRITICAL FOR SYNC
        -----------------
        Do NOT add any of these flags — they rewrite timestamps and are the
        primary cause of A/V/subtitle desynchronisation:
          -vsync cfr, -async, -copyts, -start_at_zero,
          -fflags +genpts, -fflags +discardcorrupt,
          -avoid_negative_ts, -max_interleave_delta 0,
          -err_detect ignore_err

        +genpts:  regenerates PTS even when packets already have valid
                  timestamps — causes subtitle drift.
        +discardcorrupt:  drops subtitle packets ffmpeg deems "corrupt".
        -avoid_negative_ts:  shifts streams by different amounts — the video
                  may already have been shifted during the yt-dlp merge step,
                  so re-applying here compounds the offset.
        -max_interleave_delta 0:  disables packet interleaving, so subtitle
                  packets end up clustered instead of interspersed with A/V.
                  This degrades seek accuracy and causes display lag.
        """
        video_file = task.get("filename")
        if not video_file or not os.path.exists(video_file) or not find_executable("ffmpeg"):
            # Mark completed even if we can't mux
            with self.lock:
                task["status"] = "completed"
                self._save(force=True)
            return

        subs = task.get("subtitle_files") or []
        # Backward compat: single subtitle_file
        if not subs and task.get("subtitle_file") and os.path.exists(task["subtitle_file"]):
            subs = [{"lang": "ar", "name": "Arabic", "path": task["subtitle_file"]}]

        # Nothing to do
        if not subs:
            with self.lock:
                task["status"] = "completed"
                self._save(force=True)
            return

        # Only keep existing subtitle files
        subs = [s for s in subs if s.get("path") and os.path.exists(s["path"])]
        if not subs:
            with self.lock:
                task["status"] = "completed"
                self._save(force=True)
            return

        # ── Auto-sync subtitles to the video's audio track ──────────────
        # Community subtitles are often timed for a retail Blu-ray/WEB-DL
        # release whose edit differs from the CDN HLS stream (recaps,
        # different start point, etc.).  ffsubsync detects the offset by
        # comparing speech patterns in the audio with subtitle timestamps
        # and corrects the SRT files before we mux them.
        try:
            self._sync_subtitles_to_video(video_file, subs)
        except Exception as e:
            self._log(f"Subtitle sync step failed ({e}) — continuing with original timing", level="WARNING")

        is_mp4 = video_file.lower().endswith(".mp4")
        temp_out = video_file + (".tmp.mp4" if is_mp4 else ".tmp.mkv")
        sub_codec = "mov_text" if is_mp4 else "srt"

        # ── Build minimal, sync-safe ffmpeg command ──────────────────────
        # Principle: touch nothing except adding the subtitle streams.
        # Every extra flag is a potential source of timestamp corruption.
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-y",
            "-i", video_file,
        ]
        for s in subs:
            cmd.extend(["-i", s["path"]])

        # Map: video + audio from input 0, one subtitle stream from each
        # SRT/VTT input.  Use absolute stream index (N:0) instead of type
        # selector (N:s?) for more reliable mapping on all ffmpeg builds.
        cmd.extend(["-map", "0:v", "-map", "0:a"])
        for i in range(1, 1 + len(subs)):
            cmd.extend(["-map", f"{i}:0"])

        cmd.extend(["-c:v", "copy", "-c:a", "copy", "-c:s", sub_codec])

        # Container-specific: faststart moves moov atom for better seeking
        if is_mp4:
            cmd.extend(["-movflags", "+faststart"])

        # Preserve original container metadata (title, encoder, etc.)
        cmd.extend(["-map_metadata", "0"])

        # ── Per-subtitle-track metadata (language + title + disposition) ──
        # Use ISO 639-2/T three-letter codes so MP4's mdhd atom stores them
        # correctly — two-letter codes silently fail in some ffmpeg builds.
        for idx, s in enumerate(subs):
            lang3 = self._to_iso639_2(s.get("lang") or "und")
            name  = s.get("name") or lang3
            cmd.extend([f"-metadata:s:s:{idx}", f"language={lang3}"])
            cmd.extend([f"-metadata:s:s:{idx}", f"title={name}"])
            # First subtitle track is the default; others are not.
            # Note: -disposition uses stream specifier directly (s:N = Nth
            # subtitle), unlike -metadata which needs s:s:N prefix.
            if idx == 0:
                cmd.extend([f"-disposition:s:{idx}", "default"])
            else:
                cmd.extend([f"-disposition:s:{idx}", "0"])

        cmd.append(temp_out)

        # Compute a generous mux timeout based on file size:
        # assume ~200 MB/s copy throughput as floor, min 120s, max 600s.
        try:
            file_size_mb = os.path.getsize(video_file) / (1024 * 1024)
        except OSError:
            file_size_mb = 0
        mux_timeout = max(120, min(600, int(file_size_mb / 50) + 60))

        try:
            self._log(f"Muxing subtitles for {task.get('title')}...", level="INFO")
            _mux_kw = {}
            if sys.platform == "win32":
                _mux_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            mux_ok = False
            process = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=mux_timeout, **_mux_kw)
            if process.returncode == 0 and os.path.exists(temp_out) and os.path.getsize(temp_out) > 5 * 1024 * 1024:
                mux_ok = True
            else:
                # First attempt failed — retry with lenient flags for truncated streams.
                # Only add -err_detect ignore_err (input-side) to tolerate corrupt
                # packets in the video, but do NOT add genpts/discardcorrupt/
                # avoid_negative_ts which destroy subtitle sync.
                err = (process.stderr or "").strip() or "Unknown ffmpeg error"
                self._log(f"FFmpeg mux attempt 1 failed ({err}), retrying with lenient flags...", level="WARNING")
                if os.path.exists(temp_out):
                    try:
                        os.remove(temp_out)
                    except Exception:
                        pass
                retry_cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error",
                    "-err_detect", "ignore_err",
                    "-y", "-i", video_file,
                ]
                for s in subs:
                    retry_cmd.extend(["-i", s["path"]])
                retry_cmd.extend(["-map", "0:v", "-map", "0:a"])
                for i in range(1, 1 + len(subs)):
                    retry_cmd.extend(["-map", f"{i}:0"])
                retry_cmd.extend(["-c:v", "copy", "-c:a", "copy", "-c:s", sub_codec])
                if is_mp4:
                    retry_cmd.extend(["-movflags", "+faststart"])
                retry_cmd.extend(["-map_metadata", "0"])
                for idx, s in enumerate(subs):
                    lang3 = self._to_iso639_2(s.get("lang") or "und")
                    name  = s.get("name") or lang3
                    retry_cmd.extend([f"-metadata:s:s:{idx}", f"language={lang3}"])
                    retry_cmd.extend([f"-metadata:s:s:{idx}", f"title={name}"])
                    retry_cmd.extend([f"-disposition:s:{idx}", "default" if idx == 0 else "0"])
                retry_cmd.append(temp_out)
                try:
                    p2 = subprocess.run(retry_cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=mux_timeout, **_mux_kw)
                    if p2.returncode == 0 and os.path.exists(temp_out) and os.path.getsize(temp_out) > 5 * 1024 * 1024:
                        mux_ok = True
                    else:
                        self._log(f"FFmpeg mux retry also failed for {task.get('title')}", level="WARNING")
                except Exception:
                    pass

            if mux_ok:
                # Replace original
                try:
                    os.remove(video_file)
                except Exception:
                    pass
                shutil.move(temp_out, video_file)
                self._log(f"Subtitles embedded successfully for {task.get('title')}", level="INFO")
                # Remove external subs (now embedded)
                for s in subs:
                    try:
                        os.remove(s["path"])
                    except Exception:
                        pass
            else:
                # Keep original file and external subs; clean temp output
                self._log(f"FFmpeg mux failed for {task.get('title')}: subtitles kept as external files", level="WARNING")
                if os.path.exists(temp_out):
                    try:
                        os.remove(temp_out)
                    except Exception:
                        pass

        except subprocess.TimeoutExpired:
            self._log(f"FFmpeg mux timeout ({mux_timeout}s) for {task.get('title')}", level="ERROR")
            if os.path.exists(temp_out):
                try:
                    os.remove(temp_out)
                except Exception:
                    pass
        except Exception as e:
            self._log(f"Subtitle embedding exception: {e}", level="ERROR")
            if os.path.exists(temp_out):
                try:
                    os.remove(temp_out)
                except Exception:
                    pass

        with self.lock:
            task["status"] = "completed"
            # Clear to avoid trying to re-mux
            task["subtitle_file"] = None
            task["subtitle_files"] = []
            self._save(force=True)
            self._flush_save()  # Ensure everything is persisted
