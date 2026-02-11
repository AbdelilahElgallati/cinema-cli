import json
import os
import re
import shutil
import subprocess
import threading
import time

from src.config import console
from src.ui.theme import theme
from src.utils.storage import load_json_data, save_json_data
from src.utils.utils import sanitize_filename
from src.services.subtitles import SubtitleManager
from src.core.settings import SettingsManager

DOWNLOADS_FILE = os.path.expanduser("~/.cinema-cli-downloads.json")


class DownloadManager:
    def __init__(self, source_manager=None):
        self.queue = load_json_data(DOWNLOADS_FILE) or []
        # Reset any 'downloading' status to 'pending' on startup
        for task in self.queue:
            if task["status"] == "downloading":
                task["status"] = "pending"
        self.running = False
        self.thread = None
        self.lock = threading.Lock()
        self.settings = SettingsManager()
        self.subtitle_manager = SubtitleManager()
        self.source_manager = source_manager

    # ---- Internal helpers -------------------------------------------------

    def _build_identity(self, filename, title, api_params=None, meta=None):
        """
        Build a stable identity string for a download task.

        This lets us reliably de-duplicate single-episode and batch downloads
        using semantic information (tmdb id / season / episode) instead of
        only the human-readable title.
        """
        meta = meta or {}
        api_params = api_params or {}

        tmdb_id = api_params.get("tmdb_id") or meta.get("tmdb_id")
        media_type = api_params.get("media_type") or meta.get("type")
        season = api_params.get("season") or meta.get("season")
        episode = api_params.get("episode") or meta.get("episode")

        parts = [
            str(media_type or ""),
            str(tmdb_id or ""),
            str(season or ""),
            str(episode or ""),
        ]

        identity = "|".join(parts).strip("|")
        if identity:
            return identity

        # Fallback for generic downloads: title + filename
        return f"TITLE:{title}|FILE:{filename}"

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._worker, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)

    def add_task(self, url, filename, title, subtitles=None, headers=None, meta=None, api_params=None):
        import uuid

        identity = self._build_identity(filename, title, api_params, meta)

        task = {
            "id": str(uuid.uuid4()),
            "url": url,
            "filename": filename,
            "title": title,
            "subtitles": subtitles,
            "headers": headers,
            "meta": meta,
            "api_params": api_params,
            "identity": identity,
            "status": "pending",
            "progress": 0,
            "speed": "0 B/s",
            "eta": "00:00",
            "added_at": time.time(),
        }
        with self.lock:
            # Avoid duplicates for both single and batch episodes.
            # Prefer the semantic identity if available, but gracefully
            # fall back to the legacy title-based check for older entries.
            for t in self.queue:
                status = t.get("status")
                if status not in ["pending", "downloading"]:
                    continue

                if t.get("identity") and t["identity"] == identity:
                    # console.print(f"[{theme.warning}]Task already in queue: {title}[/{theme.warning}]")
                    return

                if not t.get("identity") and t.get("title") == title:
                    # Legacy entry without identity; keep old behaviour.
                    return

            self.queue.append(task)
            self._save()
        # console.print(f"[{theme.success}]Added to download queue: {filename}[/{theme.success}]")

    def remove_task(self, task_id):
        with self.lock:
            self.queue = [t for t in self.queue if t["id"] != task_id]
            self._save()

    def retry_task(self, task_id):
        with self.lock:
            for t in self.queue:
                if t["id"] == task_id:
                    t["status"] = "pending"
                    t["progress"] = 0
            self._save()

    def get_queue(self):
        return self.queue

    def clear_completed(self):
        with self.lock:
            self.queue = [t for t in self.queue if t["status"] != "completed"]
            self._save()

    def _save(self):
        save_json_data(DOWNLOADS_FILE, self.queue)

    def _worker(self):
        while self.running:
            task = None
            with self.lock:
                pending = [t for t in self.queue if t["status"] == "pending"]
                if pending:
                    task = pending[0]

            if task:
                self._process_task(task)
            else:
                time.sleep(1)

    def _process_task(self, task):
        with self.lock:
            task["status"] = "downloading"
            self._save()

        max_retries = 3
        attempt = 0
        
        while attempt < max_retries:
            attempt += 1
            success = self._run_task_logic(task)
            if success:
                break
            
            # If we failed, wait and retry if we haven't exhausted attempts
            if attempt < max_retries:
                 time.sleep(3) # Wait before retry
            else:
                with self.lock:
                    task["status"] = "error"
                    self._save()

    def _run_task_logic(self, task):

        # Late fetching of URL to avoid expiry
        if not task.get("url") and task.get("api_params") and self.source_manager:
            # console.print(f"[{theme.accent}]Refreshing source for: {task['title']}...[/{theme.accent}]")
            params = task["api_params"]
            source, subtitles = self.source_manager.get_best_source(
                params.get("tmdb_id"),
                params.get("media_type"),
                season=params.get("season"),
                episode=params.get("episode")
            )
            if source:
                task["url"] = source.get("url")
                task["headers"] = source.get("headers")
                if not task.get("subtitles"):
                    task["subtitles"] = subtitles
                with self.lock:
                    self._save()
            else:
                return False # Trigger retry (source not found)

        # Create temp dir
        temp_dir = os.path.join(os.getcwd(), ".download_temp")
        os.makedirs(temp_dir, exist_ok=True)

        # Download subtitle (logic from downloads.py)
        sub_downloaded = False
        # Download subtitles
        try:
            meta = task.get("meta") or {}
            match_data = {}
            if meta.get("year"): match_data["year"] = meta["year"]
            if meta.get("season"): match_data["season"] = meta["season"]
            if meta.get("episode"): match_data["episode"] = meta["episode"]

            # Use configured preferred languages
            pref_langs = self.settings.subtitle_languages
            
            sub_paths = self.subtitle_manager.get_subtitles(
                task["title"],
                task.get("subtitles", []),
                match_data={
                    "series_name": meta.get("series_name") or task["title"].split(" S")[0],
                    "year": meta.get("year"),
                    "season": meta.get("season"),
                    "episode": meta.get("episode")
                },
                preferred_langs=pref_langs
            )

            # Move/Copy subtitles to video directory with proper naming
            video_path = os.path.abspath(task["filename"])
            video_dir = os.path.dirname(video_path)
            base_name = os.path.splitext(os.path.basename(video_path))[0]
            
            for path in sub_paths:
                if os.path.exists(path):
                    # Infer lang from filename (e.g., title_en.srt)
                    fname = os.path.basename(path)
                    lang_code = "und"
                    if "_" in fname:
                        lang_part = fname.split("_")[-1]
                        lang_code = lang_part.split(".")[0]
                    
                    ext = os.path.splitext(path)[1]
                    target_name = f"{base_name}.{lang_code}{ext}"
                    target_path = os.path.join(video_dir, target_name)
                    try:
                        shutil.copy(path, target_path)
                    except:
                        pass
        except Exception:
             pass

        # Prepare yt-dlp command
        url = task["url"]
        mp4_out = task["filename"]

        cmd = [
            "yt-dlp",
            url,
            "-o",
            mp4_out,
            "-P",
            f"temp:{temp_dir}",
            "--no-part",
            "--hls-prefer-native",
            "--concurrent-fragments",
            "16",
            "--newline",  # Important for parsing progress
            "--no-warnings",
        ]

        if shutil.which("aria2c"):
            cmd.extend(
                [
                    "--downloader",
                    "aria2c",
                    "--downloader-args",
                    "aria2c:-x 16 -s 16 -k 1M",
                ]
            )

        if task.get("headers"):
            headers = task["headers"]
            ua = headers.get("User-Agent") or headers.get("user-agent")
            if ua:
                cmd.extend(["--user-agent", ua])
            ref = headers.get("Referer") or headers.get("referer")
            if ref:
                cmd.extend(["--referer", ref])

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding="utf-8",  # Ensure encoding is set
            )

            last_save = 0
            # Parse output for progress
            while True:
                line = process.stdout.readline()
                if not line:
                    break

                # Parse yt-dlp output [download] 5.0% of 100.00MiB at 10.00MiB/s ETA 00:10
                if "[download]" in line and "%" in line:
                    try:
                        percent_str = line.split("%")[0].split()[-1]
                        percent = float(percent_str)

                        # Extract other info if possible
                        speed = "Unknown"
                        if "at" in line:
                            parts = line.split("at")
                            if len(parts) > 1:
                                speed = parts[1].split("ETA")[0].strip()

                        eta_val = task.get("eta", "00:00")
                        if "ETA" in line:
                            try:
                                eta_part = line.split("ETA")[-1].strip()
                                eta_val = eta_part.split()[0]
                            except Exception:
                                pass

                        with self.lock:
                            task["progress"] = percent
                            task["speed"] = speed
                            task["eta"] = eta_val
                            # Save every 5 seconds or if finished
                            if time.time() - last_save > 5:
                                self._save()
                                last_save = time.time()
                    except Exception:
                        pass

            process.wait()

            with self.lock:
                if process.returncode == 0 or task["progress"] >= 99:
                    task["status"] = "completed"
                    task["progress"] = 100
                    self._save()
                    
                    # Post-download processing (only on success)
                    try:
                        self._organize_download(task, temp_dir)
                        self._embed_subtitles(task)
                    except Exception:
                        pass
                    
                    return True # Success
                else:
                    return False # Failed, trigger retry
        except Exception:
            return False

    def get_queue(self):
        with self.lock:
            return list(self.queue)

    def _organize_download(self, task, temp_dir):
        """Auto-create series/season directories and move downloaded files there.
        Structure: downloads/tv/SeriesName/Season XX/ or downloads/movies/MovieName/
        """
        meta = task.get("meta") or {}
        title = task.get("title") or ""
        filename = task.get("filename") or ""

        # Candidate paths where yt-dlp might have saved the file
        candidates = [
            filename,
            os.path.join(os.getcwd(), filename),
            os.path.join(temp_dir, filename),
            os.path.join(os.getcwd(), os.path.basename(filename)),
            os.path.join(temp_dir, os.path.basename(filename)),
        ]

        file_path = None
        for p in candidates:
            if p and os.path.exists(p):
                file_path = os.path.abspath(p)
                break

        if not file_path:
            return

        base_name = os.path.splitext(os.path.basename(file_path))[0]

        # Determine destination folder (use library path if configured)
        lib_paths = self.settings.local_library_paths
        if lib_paths:
            downloads_root = lib_paths[0]
        else:
            downloads_root = os.path.join(os.getcwd(), "downloads")

        # Use meta.type if available, otherwise infer from title pattern
        media_type = meta.get("type") if meta else None
        if not media_type:
            # Infer from title pattern (e.g., "Show Name S01E01")
            import re

            if re.search(r"S\d{1,2}E\d{1,2}", title):
                media_type = "tv"
            else:
                media_type = "movie"

        if media_type == "tv":
            import re

            m = re.match(r"^(.*?)\sS\d{1,2}E\d{1,2}", title)
            if m:
                series = m.group(1)
            else:
                series = title.split(" - ")[0] if title else "Series"

            season = meta.get("season") if meta else 0
            season = season or 0
            dest_dir = os.path.join(
                downloads_root,
                "tv",
                sanitize_filename(series),
                f"Season {int(season):02d}",
            )
        else:
            movie_name = title.split(" - ")[0] if title else "Movie"
            dest_dir = os.path.join(
                downloads_root, "movies", sanitize_filename(movie_name)
            )

        os.makedirs(dest_dir, exist_ok=True)

        dest_path = os.path.join(dest_dir, os.path.basename(file_path))
        try:
            shutil.move(file_path, dest_path)

            # Move all related subtitle files
            src_dir = os.path.dirname(file_path)
            if os.path.exists(src_dir):
                for f in os.listdir(src_dir):
                    # Check if file starts with base_name and has subtitle extension
                    if f.startswith(base_name) and f != os.path.basename(file_path) and f.lower().endswith(('.srt', '.vtt', '.ass', '.sub')):
                        src_sub = os.path.join(src_dir, f)
                        dst_sub = os.path.join(dest_dir, f)
                        try:
                            shutil.move(src_sub, dst_sub)
                        except Exception:
                            pass

            # Update task filename in queue
            with self.lock:
                task["filename"] = dest_path
                self._save()

            # console.print(f"[{theme.success}]Moved to {dest_dir}[/{theme.success}]")
        except Exception:
            pass

    def _embed_subtitles(self, task):
        """Embed subtitle files into MP4 using ffmpeg with proper metadata."""
        video_file = task.get("filename")

        if not video_file or not os.path.exists(video_file):
            return

        # Check if ffmpeg is available
        if not shutil.which("ffmpeg"):
            return

        base_name = os.path.splitext(os.path.basename(video_file))[0]
        video_dir = os.path.dirname(video_file)
        
        # Find all subs
        subs = []
        if os.path.exists(video_dir):
            for f in os.listdir(video_dir):
                if f.startswith(base_name) and f.lower().endswith(('.srt', '.vtt', '.ass', '.sub')):
                    subs.append(os.path.join(video_dir, f))
        
        if not subs:
            return

        # Create temp output file
        temp_output = video_file + ".tmp.mp4"

        try:
            # ffmpeg command construction
            cmd = ["ffmpeg", "-i", video_file]
            for sub in subs:
                cmd.extend(["-i", sub])

            # Map streams: source video/audio + all subs
            cmd.extend(["-map", "0", "-c", "copy"]) # Copy video/audio streams
            
            # Map and configure each subtitle stream
            for i, sub_path in enumerate(subs):
                input_idx = i + 1
                cmd.extend(["-map", str(input_idx)])
                
                # Determine codec
                sub_ext = os.path.splitext(sub_path)[1].lower()
                codec = "mov_text" if sub_ext in [".srt", ".vtt"] else "copy"
                cmd.extend([f"-c:s:{i}", codec])
                
                # Determine language from filename (base.lang.ext or base_lang.ext)
                fname = os.path.splitext(os.path.basename(sub_path))[0]
                lang = "und"
                # Check for .lang suffix
                if "." in fname:
                    code = fname.split(".")[-1].lower()
                    if len(code) in [2, 3]:
                        mapping = {'ar': 'ara', 'en': 'eng', 'fr': 'fre', 'es': 'spa'}
                        lang = mapping.get(code, code)
                elif "_" in fname:
                    code = fname.split("_")[-1].lower()
                    if len(code) in [2, 3]:
                        mapping = {'ar': 'ara', 'en': 'eng', 'fr': 'fre', 'es': 'spa'}
                        lang = mapping.get(code, code)
                
                cmd.extend([f"-metadata:s:s:{i}", f"language={lang}"])
                
                # Set disposition: make first one default?
                # logic: if this lang matches our primary preference, set default
                # But simple logic: first valid one is default
                if i == 0:
                     cmd.extend([f"-disposition:s:{i}", "default"])
                else:
                     cmd.extend([f"-disposition:s:{i}", "0"])
                     
            cmd.extend(["-y", "-loglevel", "error", temp_output])

            # Run ffmpeg
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )

            stdout, stderr = process.communicate()

            if process.returncode == 0:
                # Replace original with embedded version
                os.remove(video_file)
                shutil.move(temp_output, video_file)

                # Delete the subtitle files since they are now embedded
                for f_sub in os.listdir(video_dir):
                    if f_sub.startswith(base_name) and f_sub.lower().endswith(('.srt', '.vtt', '.ass', '.sub')):
                         try:
                             os.remove(os.path.join(video_dir, f_sub))
                         except:
                             pass

                # console.print(f"[{theme.success}]Embedded {len(subs)} subtitle tracks.[/{theme.success}]")
            else:
                # Clean up temp file on failure
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                # console.print(f"[{theme.warning}]ffmpeg error: {stderr[:200]}[/{theme.warning}]")
        except Exception:
            # Silence background errors for clean UI
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except Exception:
                    pass
