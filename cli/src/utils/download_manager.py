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
import queue
import signal
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from collections import deque

from src.config import DOWNLOAD_LOG, SUCCESS, TEXT, WARNING, console
from src.utils.app_logger import log_event
from src.utils.storage import load_json_data, save_json_data
from src.utils.utils import sanitize_filename
from src.utils.subtitles import fetch_subtitles

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DOWNLOADS_FILE = os.path.expanduser("~/.cinema-cli-downloads.json")


class DownloadManager:
    def __init__(self, max_workers=1, downloads_dir=None, api_client=None):
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
        self.downloads_dir = downloads_dir or os.path.join(os.path.expanduser("~"), "Downloads", "cinema-cli")
        # Use user's temp directory instead of os.getcwd() to avoid system32 issues
        self.temp_dir = os.path.join(tempfile.gettempdir(), "cinema-cli-temp")
        
        # Throttled save mechanism to reduce disk I/O
        self._last_save_time = 0
        self._save_interval = 0.5  # Min seconds between saves
        self._pending_save = False
        
        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Ensure clean shutdown on Ctrl+C or termination."""
        def signal_handler(signum, frame):
            self._log("Shutdown signal received, stopping download manager...", level="INFO")
            self.stop()
            sys.exit(0)
        
        try:
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
        except (ValueError, OSError):
            # May fail on some platforms or if signals already set
            pass

    def start(self):
        if not self.running:
            self.running = True
            threading.Thread(target=self._queue_listener, daemon=True).start()

    def stop(self):
        self.running = False
        # Flush any pending saves
        try:
            self._flush_save()
        except Exception:
            pass
        # Cancel pending futures
        for tid, future in list(self.active_tasks.items()):
            try:
                if not future.done():
                    future.cancel()
            except Exception:
                pass
        self.executor.shutdown(wait=False)

    def _log(self, message, level="INFO"):
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(DOWNLOAD_LOG, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] [{level}] {message}\n")
            log_event("download", message, level=level)
        except Exception:
            pass

    def add_task(self, url, filename, title, subtitles=None, headers=None, meta=None, fallback_sources=None, api_params=None, preferred_sub_lang='ar', include_all_subs=True, fallback_sub_langs=None):
        task = {
            "id": str(uuid.uuid4()),
            "url": url,
            "filename": filename,
            "title": title,
            "subtitles": subtitles,
            "preferred_sub_lang": preferred_sub_lang,
            "include_all_subs": include_all_subs,
            "fallback_sub_langs": fallback_sub_langs,
            "headers": headers,
            "meta": meta,
            "fallback_sources": fallback_sources or [],
            "api_params": api_params,
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
        with self.lock:
            self.queue.append(task)
            self._save()
        console.print(f"[green]Added to download queue: {title}[/green]")
        time.sleep(1)

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
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry

        session = requests.Session()
        try:
            retry = Retry(
                total=5,
                connect=5,
                read=5,
                backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["HEAD", "GET"],
            )
        except TypeError:
            retry = Retry(
                total=5,
                connect=5,
                read=5,
                backoff_factor=0.5,
                status_forcelist=[429, 500, 502, 503, 504],
                method_whitelist=["HEAD", "GET"],
            )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=10,
            pool_maxsize=20,
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

    def _queue_listener(self):
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

            time.sleep(2)

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

    def _process_task(self, task):
        """Robust task processing with parallel subtitle download and multiple fallback layers."""
        temp_dir = self.temp_dir
        os.makedirs(temp_dir, exist_ok=True)
        
        # Use unique temp filename to avoid collisions
        temp_id = str(uuid.uuid4())[:8]
        # Sanitize the filename to avoid path issues
        safe_fname = sanitize_filename(os.path.splitext(task['filename'])[0]) + ".mp4"
        mp4_out = os.path.join(temp_dir, f"{temp_id}_{safe_fname}")

        # Start subtitle download in background thread (parallel with video)
        subtitle_future = None
        if hasattr(self, "_download_subtitles") and task.get("subtitles"):
            subtitle_executor = ThreadPoolExecutor(max_workers=1)
            subtitle_future = subtitle_executor.submit(self._download_subtitles, task, temp_dir)
            self._log("Started subtitle download in background", level="INFO")

        max_attempts = 3
        success = False
        last_error = ""

        for attempt in range(max_attempts):
            if not self.running:
                return  # Graceful shutdown

            if attempt > 0:
                self._log(
                    f"Retry {attempt + 1}/{max_attempts} for {task['title']}...", 
                    level="INFO"
                )
                time.sleep(3)

            try:
                # Refresh source if needed
                refreshed_url = self._refresh_source_if_needed(task, attempt)
                
                # Build source list with priority
                sources_to_try = self._build_source_list(task, refreshed_url)
                
                if not sources_to_try:
                    last_error = "No valid sources"
                    continue

                # Try each source
                for i, source in enumerate(sources_to_try):
                    if not self.running:
                        return
                        
                    success = self._try_download_source(
                        source, mp4_out, task, attempt, i
                    )
                    
                    if success:
                        break
                
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

    def _find_actual_output(self, temp_dir, temp_id, expected_path):
        """Find the actual output file yt-dlp created (may differ in extension)."""
        base_no_ext = os.path.splitext(os.path.basename(expected_path))[0]
        # Search for files starting with our temp_id prefix
        for fname in os.listdir(temp_dir):
            if fname.startswith(temp_id) and not fname.endswith(('.part', '.ytdl', '.temp')):
                full = os.path.join(temp_dir, fname)
                if os.path.isfile(full) and os.path.getsize(full) > 1024 * 1024:
                    self._log(f"Found actual output: {fname}", level="INFO")
                    return full
        return None

    def _extract_url(self, value):
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

    def _download_subtitles(self, task, temp_dir):
        """Download subtitles in parallel for faster performance."""
        # If already downloaded, skip
        if task.get("subtitle_files") or task.get("subtitle_file"):
            return

        # Subtitle preferences
        preferred = (task.get("preferred_sub_lang") or "ar").strip().lower()
        include_all = bool(task.get("include_all_subs", True))
        fallback_langs = task.get("fallback_sub_langs")

        def norm_lang(lang: str) -> str:
            l = (lang or "").strip().lower()
            # common mappings
            if l in ["arabic", "ara", "ar"]:
                return "ar"
            if l in ["english", "eng", "en"]:
                return "en"
            if l in ["french", "fre", "fra", "fr"]:
                return "fr"
            if l in ["spanish", "spa", "es"]:
                return "es"
            if len(l) == 3 and l.endswith("a") and l[:2] in ["ar", "en", "fr", "es"]:
                return l[:2]
            return l or "und"

        def display_lang(code: str) -> str:
            m = {
                "ar": "Arabic",
                "en": "English",
                "fr": "French",
                "es": "Spanish",
                "und": "Unknown",
            }
            return m.get(code, code)

        # Normalize
        preferred = norm_lang(preferred)
        if preferred == "none":
            return

        # Build subtitle list from API payload
        subs = task.get("subtitles") or []
        # normalize and filter items that have url
        items = []
        for s in subs:
            if isinstance(s, dict) and s.get("url"):
                items.append({
                    "lang": norm_lang(s.get("lang") or s.get("language") or s.get("code")),
                    "url": s.get("url"),
                })

        # If API didn't provide subtitles, fall back to OpenSubtitles (multi-language)
        if not items:
            try:
                yr = sn = epn = None
                meta = task.get("meta") or {}
                if isinstance(meta, dict):
                    yr = meta.get("year")
                    sn = meta.get("season")
                    epn = meta.get("episode")

                langs = []
                if isinstance(fallback_langs, (list, tuple)):
                    langs = [str(x).strip().lower() for x in fallback_langs if str(x).strip()]
                if not langs:
                    langs = [preferred, "ar", "en"]

                subs_found = fetch_subtitles(task.get("title") or "", langs, year=yr, season=sn, episode=epn)
                if subs_found:
                    base, _ = os.path.splitext(task.get("filename") or task.get("title") or "video")
                    base = os.path.basename(base)
                    downloaded = []
                    for s in subs_found:
                        lang = norm_lang(str(s.get("lang") or "und"))
                        ext = str(s.get("ext") or "srt")
                        sub_filename = os.path.join(temp_dir, f"{base}.{lang}.{ext}")
                        with open(sub_filename, "wb") as f:
                            f.write(s.get("content") or b"")
                        downloaded.append({"lang": lang, "name": display_lang(lang), "path": sub_filename})
                        if not include_all:
                            break
                    if downloaded:
                        downloaded.sort(key=lambda x: (0 if x["lang"] == preferred else 1, x["lang"]))
                        with self.lock:
                            task["subtitle_files"] = downloaded
                            task["subtitle_file"] = downloaded[0]["path"]
                            self._save(force=True)
                    return
            except Exception as e:
                self._log(f"OpenSubtitles fallback failed: {e}", level="WARNING")
            return

        preferred_items = [x for x in items if x["lang"] == preferred]
        ordered = []
        if preferred_items:
            ordered.append(preferred_items[0])

        if include_all:
            seen = {x["url"] for x in ordered}
            seen_lang = {x["lang"] for x in ordered}
            for x in items:
                if x["url"] in seen or x["lang"] in seen_lang:
                    continue
                ordered.append(x)
                seen.add(x["url"])
                seen_lang.add(x["lang"])
        elif not ordered and items:
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
                sub_ext = "vtt" if ".vtt" in sub_url.lower() else "srt"
                sub_filename = os.path.join(temp_dir, f"{base}.{sub_lang}.{sub_ext}")
                
                resp = requests.get(sub_url, timeout=15, verify=False, headers=task.get("headers") or {})
                resp.raise_for_status()
                
                # Basic validation: avoid HTML error pages
                content_type = (resp.headers.get("content-type") or "").lower()
                if "text/html" in content_type and len(resp.content) < 8000:
                    return None
                
                # Decode robustly and save UTF-8
                decoded = None
                for enc in ["utf-8", "utf-8-sig", "cp1256", "windows-1256", "iso-8859-6", "latin-1"]:
                    try:
                        decoded = resp.content.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                
                with open(sub_filename, "w", encoding="utf-8-sig") as f:
                    f.write(decoded if decoded is not None else resp.content.decode("utf-8", errors="ignore"))
                
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
        """Refresh streaming source from API if available."""
        if not (task.get("api_params") and self.api_client and attempt > 0):
            return None
            
        try:
            p = task["api_params"]
            self._log(f"Refreshing source (Attempt {attempt + 1})...", level="INFO")
            
            data = self.api_client.get_sources_api(
                p.get("tmdb_id"), 
                p.get("media_type"), 
                p.get("season"), 
                p.get("episode")
            )
            
            files = data.get("files", [])
            if files:
                task["url"] = files[0].get("file")
                task["headers"] = files[0].get("headers")
                task["fallback_sources"] = files[1:] if len(files) > 1 else []
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

    def _try_download_source(self, source, output_path, task, attempt_num, source_idx):
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
            
            if not success:
                # Fallback to direct download
                success = self._direct_download(url, output_path, source.get("headers"), task)
            
            # Validate result - check the expected path or find yt-dlp's actual output
            if success:
                if os.path.exists(output_path) and self._validate_download(output_path):
                    return True
                # yt-dlp may have written to a different extension
                temp_dir = os.path.dirname(output_path)
                base_prefix = os.path.basename(output_path).split("_")[0]  # temp_id
                for fname in os.listdir(temp_dir):
                    if fname.startswith(base_prefix) and not fname.endswith(('.part', '.ytdl', '.temp')):
                        candidate = os.path.join(temp_dir, fname)
                        if candidate != output_path and os.path.isfile(candidate) and self._validate_download(candidate):
                            # Rename to expected output_path
                            try:
                                shutil.move(candidate, output_path)
                                return True
                            except Exception:
                                return True  # file exists at candidate
                
                self._safe_remove(output_path)
                return False
            else:
                self._safe_remove(output_path)
                return False
                
        except Exception as e:
            self._log(f"Source {source_idx + 1} failed: {e}", level="ERROR")
            self._safe_remove(output_path)
            return False

    def _download_with_ytdlp(self, url, output_path, task, source, is_worker):
        """Execute yt-dlp with optimized parameters for faster downloads."""
        if not shutil.which("yt-dlp"):
            self._log("yt-dlp not found in PATH, skipping", level="WARNING")
            return False

        # Aggressive optimization for fragment concurrency
        # Workers get moderate concurrency, regular sources get maximum
        fragments = os.getenv("YTDLP_CONCURRENT_FRAGMENTS") or ("8" if is_worker else "64")
        
        output_path = os.path.abspath(output_path)
        output_dir = os.path.dirname(output_path)

        cmd = [
            "yt-dlp",
            url,
            "-o", output_path,
            "--paths", f"temp:{output_dir}",
            "--merge-output-format", "mp4",
            "--newline",
            "--no-warnings",
            "--hls-prefer-native",
            "--no-check-certificates",
            "--fragment-retries", "20",
            "--retry-sleep", "0.5",
            "--concurrent-fragments", fragments,
            "--socket-timeout", "15",
            "--retries", "8",
            "--no-playlist",
            # Ensure frame-accurate merging and A/V sync
            "--hls-use-mpegts",  # Use MPEG-TS for better fragment handling
            "--fixup", "detect_or_warn",
            "--prefer-ffmpeg",
            "--ffmpeg-location", shutil.which("ffmpeg") or "ffmpeg",
            # Aggressive performance optimizations
            "--buffer-size", "1M",
            "--http-chunk-size", "50M",
            # Post-processing: only fix negative timestamps; do NOT touch frame/sample timing.
            # -vsync cfr / -async 1 duplicate/drop frames and resample audio, causing A/V desync.
            "--postprocessor-args", "ffmpeg:-avoid_negative_ts make_non_negative -max_interleave_delta 0",
            # Skip unnecessary metadata processing
            "--no-write-description",
            "--no-write-info-json",
            "--no-write-thumbnail",
        ]
        
        # Add aria2c if available (much faster for HLS/fragmented streams)
        if shutil.which("aria2c"):
            # Aggressive connection settings for maximum speed
            conn = os.getenv("ARIA2C_CONNECTIONS") or ("12" if is_worker else "64")
            cmd.extend([
                "--downloader", "aria2c",
                "--downloader-args", f"aria2c:-x {conn} -s {conn} -k 5M --file-allocation=none --async-dns=false --max-tries=10 --retry-wait=1 --timeout=30 --connect-timeout=10 --split={conn}"
            ])
        
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

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace"
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

            last_progress_time = time.time()
            start_time = time.time()
            max_duration = 7200  # 2 hours max
            stall_timeout = 300  # 5 minutes without progress = stall
            muxing_started = False
            
            while True:
                # 1. Check overall timeout
                if time.time() - start_time > max_duration:
                    self._log("Download timeout exceeded", level="ERROR")
                    process.terminate()
                    return False
                
                # 2. Check for stall (but be more lenient during muxing - 10 min)
                effective_stall_timeout = 600 if muxing_started else stall_timeout
                if time.time() - last_progress_time > effective_stall_timeout:
                    self._log(f"Download stalled (no output for {effective_stall_timeout//60} minutes)", level="ERROR")
                    with self.lock:
                        task["status_message"] = "STALLED"
                        self._save()
                    process.terminate()
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
                            # Detect muxing phase
                            if "[ffmpeg]" in stripped or "[Merger]" in stripped or "[FixupM3u8]" in stripped:
                                muxing_started = True
                                last_progress_time = time.time()  # Reset timer for mux
                                with self.lock:
                                    task["status"] = "muxing"
                                    task["status_message"] = "Muxing..."
                                    self._save()
                            if self._parse_progress_line(stripped, task):
                                last_progress_time = time.time()
                        output_queue.task_done()
                except queue.Empty:
                    pass
                
                if not self.running:
                    process.terminate()
                    return False
                    
                time.sleep(0.1)
            
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

    def _parse_progress_line(self, line, task):
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
                    # "of ~10MiB" for total
                    total_match = re.search(r"of\s+(~?[\d.]+\s*[KMG]?i?B)", line, re.IGNORECASE)
                    if total_match:
                        tb = self._parse_size_to_bytes(total_match.group(1))
                        if tb > 0:
                            task["_bytes_total"] = tb
                            # Estimate downloaded from progress
                            pct = task.get("progress", 0)
                            if pct > 0:
                                task["_bytes_downloaded"] = int((pct / 100) * tb)
                        updated = True

                # ── Speed from yt-dlp (used until we have enough byte samples) ──
                speed_match = re.search(r"at\s+([\d.]+\s*[KMG]?i?B/s)", line, re.IGNORECASE)
                if not speed_match:
                    speed_match = re.search(r"\s([\d.]+[KMG]?i?B/s)", line, re.IGNORECASE)
                if speed_match:
                    raw_speed = speed_match.group(1).replace(" ", "")
                    # Parse to bytes/s for real tracking
                    spd_bps = self._parse_size_to_bytes(raw_speed.replace("/s", ""))
                    if spd_bps > 0:
                        task["_speed_bps"] = spd_bps
                        task["speed"] = raw_speed
                    updated = True

                # ── Compute real speed & ETA from byte samples ──
                if updated:
                    self._update_real_speed(task)
                    self._update_display_fields(task)

                # ── Muxing phase ──
                if "[Merger]" in line or "[ffmpeg]" in line or "Merging" in line or "[FixupM3u8]" in line:
                    task["status_message"] = "Muxing..."
                    task["status"] = "muxing"
                    if task.get("progress", 0) < 99:
                        task["progress"] = 99
                    task["speed"] = "---"
                    task["eta"] = "muxing"
                    self._save(force=True)
                    return True

                # ── Destination (download starting) ──
                if "Destination:" in line:
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

    def _validate_download(self, file_path):
        """Validate that downloaded file is valid media."""
        if not os.path.exists(file_path):
            return False
            
        size = os.path.getsize(file_path)
        
        # Must be at least 2MB for a video (1MB is too small usually)
        if size < 2 * 1024 * 1024:
            self._log(f"Validation failed: File too small ({size / 1024:.1f} KB)", level="WARNING")
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
                
            return True
            
        except Exception as e:
            self._log(f"Validation error: {e}", level="WARNING")
            # If we can't validate but file is big, assume OK
            return size > 20 * 1024 * 1024  # 20MB+

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
        except:
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
        except:
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
        except:
            return "---"

    def _direct_download(self, url, output_path, headers, task):
        """Robust direct download with range request support."""
        download_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "*/*",
            "Connection": "keep-alive"
        }
        if headers:
            download_headers.update(headers)

        try:
            # Probe server capabilities (HEAD can be blocked; fall back to GET Range)
            with self._build_session() as session:
                try:
                    head_resp = session.head(
                        url,
                        headers=download_headers,
                        timeout=15,
                        allow_redirects=True,
                        verify=False,
                    )
                except Exception:
                    head_resp = None

                if head_resp is None or head_resp.status_code >= 400:
                    probe_headers = download_headers.copy()
                    probe_headers["Range"] = "bytes=0-1024"
                    head_resp = session.get(
                        url,
                        headers=probe_headers,
                        timeout=15,
                        allow_redirects=True,
                        stream=True,
                        verify=False,
                    )

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

    def _parallel_range_download(self, url, output_path, headers, total_size, task):
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
                with self._build_session() as session:
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
                            for chunk in resp.iter_content(chunk_size=512 * 1024):  # 512 KB → fewer Python callbacks
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
                results = [f.result(timeout=300) for f in as_completed(futures)]
                
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

    def _single_threaded_download(self, url, output_path, headers, task, expected_size=0, resume_from=0):
        """Reliable single-threaded fallback download."""
        try:
            if resume_from > 0:
                headers = headers.copy()
                headers["Range"] = f"bytes={resume_from}-"

            with self._build_session() as session:
                with session.get(
                    url,
                    headers=headers,
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
                    if resume_from > 0 and resp.status_code == 206:
                        total = resume_from + total
                    downloaded = resume_from if resume_from > 0 else 0
                    last_update = time.time()
                    
                    mode = 'ab' if resume_from > 0 else 'wb'
                    with open(output_path, mode) as f:
                        for chunk in resp.iter_content(chunk_size=4 * 1024 * 1024):  # 4 MB → fewer syscalls
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
                
        except Exception as e:
            if "timeout" in str(e).lower():
                self._log("Download timeout", level="ERROR")
                return False
            self._log(f"Single-threaded download error: {e}", level="ERROR")
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

    def _finalize_task(self, task, success, temp_path, error_msg):
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
            except Exception as e:
                self._log(f"Post-processing failed: {e}", level="ERROR")
                with self.lock:
                    task["status"] = "error"
                    task["error_log"] = f"Post-processing: {str(e)}"
                    self._save(force=True)

    def get_queue(self):
        with self.lock:
            return sorted(self.queue, key=lambda x: (
                0 if x["status"] == "downloading" else
                0 if x["status"] == "muxing" else
                1 if x["status"] == "pending" else
                2 if x["status"] == "error" else 3,
                x.get("added_at", 0)
            ))

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
            self.queue = [t for t in self.queue if t["id"] != task_id]
            self._save()
            return True

    def clear_completed(self):
        with self.lock:
            self.queue = [t for t in self.queue if t["status"] != "completed"]
            self._save()
            return True

    
    def _organize_download(self, task, temp_file_path):
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
        except Exception:
            pass

        with self.lock:
            task["filename"] = dest_path
            if moved_subs:
                task["subtitle_files"] = moved_subs
                task["subtitle_file"] = moved_subs[0]["path"]
            self._save()

    
    def _embed_subtitles(self, task):
        """Embed subtitle tracks into the final media file with optimized ffmpeg.
        - Downloads all subtitle tracks in parallel if needed.
        - Sets the first subtitle (preferred) as default.
        - Uses multi-threaded ffmpeg for faster muxing.
        """
        video_file = task.get("filename")
        if not video_file or not os.path.exists(video_file) or not shutil.which("ffmpeg"):
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

        is_mp4 = video_file.lower().endswith(".mp4")
        temp_out = video_file + (".tmp.mp4" if is_mp4 else ".tmp.mkv")

        def _codec_for_sub(path: str) -> str:
            # ffmpeg will decode .srt/.vtt fine; choose container codec
            return "mov_text" if is_mp4 else "srt"

        # Build ultra-optimized ffmpeg command for fastest muxing
        # IMPORTANT: this is a pure-copy remux (no frame/sample manipulation).
        # Do NOT add -vsync cfr, -async, -copyts, or -start_at_zero here —
        # they rewrite frame/sample timestamps and are the primary cause of
        # A/V desynchronisation and perceptible "frame delays" during playback.
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-threads", "0",        # auto-detect optimal thread count
            "-fflags", "+genpts",   # generate missing PTS; do NOT use +igndts (breaks B-frame order)
            "-y",
            "-i", video_file
        ]
        for s in subs:
            cmd.extend(["-i", s["path"]])

        # Map streams: keep video+audio from input 0, then map subtitle streams
        cmd.extend(["-map", "0:v?", "-map", "0:a?"])
        for i in range(1, 1 + len(subs)):
            cmd.extend(["-map", f"{i}:s?"])

        cmd.extend(["-c:v", "copy", "-c:a", "copy", "-c:s", _codec_for_sub(video_file)])

        # Pure-copy safe flags: interleaving + non-negative TS guard only
        cmd.extend([
            "-max_interleave_delta", "0",          # proper A/V/S interleaving without reordering
            "-avoid_negative_ts", "make_non_negative",  # fix streams that start negative
        ])

        # Container-specific optimisation flags for speed
        if is_mp4:
            cmd.extend([
                "-movflags", "+faststart",
            ])

        # Skip unnecessary processing
        cmd.extend([
            "-map_metadata", "0",   # copy only main metadata
            "-write_tmcd", "0",     # skip timecode track
        ])

        # Metadata per subtitle stream
        for idx, s in enumerate(subs):
            lang = (s.get("lang") or "und").lower()
            name = s.get("name") or lang
            cmd.extend([f"-metadata:s:s:{idx}", f"language={lang}"])
            cmd.extend([f"-metadata:s:s:{idx}", f"title={name}"])
            # set default for first track only
            if idx == 0:
                cmd.extend([f"-disposition:s:{idx}", "default"])
            else:
                cmd.extend([f"-disposition:s:{idx}", "0"])

        cmd.append(temp_out)

        try:
            self._log(f"Muxing subtitles for {task.get('title')}...", level="INFO")
            process = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=120)
            if process.returncode == 0 and os.path.exists(temp_out) and os.path.getsize(temp_out) > 5 * 1024 * 1024:
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
                # Keep original file; clean temp output
                err = (process.stderr or "").strip() or "Unknown ffmpeg error"
                self._log(f"FFmpeg mux failed for {task.get('title')}: {err}", level="WARNING")
                if os.path.exists(temp_out):
                    try:
                        os.remove(temp_out)
                    except Exception:
                        pass

        except subprocess.TimeoutExpired:
            self._log(f"FFmpeg mux timeout for {task.get('title')}", level="ERROR")
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
