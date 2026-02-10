import json
import os
import re
import shutil
import subprocess
import threading
import time

from src.config import SUCCESS, TEXT, WARNING, console
from src.utils.storage import load_json_data, save_json_data
from src.utils.subtitles import fetch_subtitle
from src.utils.utils import sanitize_filename

import logging

DOWNLOADS_FILE = os.path.expanduser("~/.cinema-cli-downloads.json")
LOG_FILE = os.path.expanduser("~/.cinema-cli/download.log")

# Setup logging
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    filemode="a",
)
logger = logging.getLogger("DownloadManager")


class DownloadManager:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.queue = load_json_data(DOWNLOADS_FILE) or []
        # Reset any 'downloading' status to 'pending' on startup
        for task in self.queue:

            if task.get("status") == "downloading":
                task["status"] = "pending"
        self.running = False
        self.thread = None
        self.lock = threading.Lock()

    def start(self):
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._worker, daemon=True)
            self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)

    def add_task(self, url, filename, title, subtitles=None, headers=None, meta=None):
        import uuid

        task = {
            "id": str(uuid.uuid4()),
            "url": url,
            "filename": filename,
            "title": title,
            "subtitles": subtitles,
            "headers": headers,
            "meta": meta,
            "status": "pending",
            "progress": 0,
            "speed": "",
            "eta": "",
            "downloaded_size": "",
            "total_size": "",
            "added_at": time.time(),
        }
        with self.lock:
            self.queue.append(task)
            self._save()
        console.print(f"[green]✓ Added to queue: {title}[/green]")
        time.sleep(0.5)

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
                    t["speed"] = ""
                    t["eta"] = ""
                    t["downloaded_size"] = ""
                    t["total_size"] = ""
            self._save()

    def clear_completed(self):
        with self.lock:
            self.queue = [t for t in self.queue if t["status"] != "completed"]
            self._save()

    def _save(self):
        save_json_data(DOWNLOADS_FILE, self.queue)

    def get_queue(self):
        with self.lock:
            return list(self.queue)

    def get_stats(self):
        with self.lock:
            active = sum(1 for t in self.queue if t["status"] == "downloading")
            pending = sum(1 for t in self.queue if t["status"] == "pending")
            completed = sum(1 for t in self.queue if t["status"] == "completed")
            failed = sum(1 for t in self.queue if t["status"] == "error")
            return {
                "active": active,
                "pending": pending,
                "completed": completed,
                "failed": failed,
                "total": len(self.queue),
            }

    # ──────────────────────────────────────────────────────────
    # Worker
    # ──────────────────────────────────────────────────────────
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

    # ──────────────────────────────────────────────────────────
    # Process a single download task
    # ──────────────────────────────────────────────────────────
    def _process_task(self, task):
        logger.info(f"Starting task: {task['title']} (ID: {task['id']})")
        logger.debug(f"Task details: {task}")
        with self.lock:
            task["status"] = "downloading"
            task["progress"] = 0
            self._save()

        temp_dir = os.path.join(os.path.expanduser("~"), ".cinema-cli", "temp")
        os.makedirs(temp_dir, exist_ok=True)
        logger.debug(f"Using temp dir: {temp_dir}")

        # ── Step 1: Download subtitle to temp dir ─────────────
        try:
            sub_path = self._download_subtitle(task, temp_dir)
            if sub_path:
                logger.info(f"Downloaded subtitle to: {sub_path}")
            else:
                logger.warning("No subtitle downloaded")
        except Exception as e:
            logger.error(f"Error downloading subtitle: {e}")
            sub_path = None

        # ── Step 2: Download video with yt-dlp ────────────────
        url = task["url"]
        mp4_out = task["filename"]

        # Ensure .mp4 extension
        base_out, ext_out = os.path.splitext(mp4_out)
        if ext_out.lower() not in (".mp4", ".mkv"):
            mp4_out = base_out + ".mp4"
            task["filename"] = mp4_out

        cmd = [
            "yt-dlp",
            url,
            "-o", mp4_out,
            "-P", f"temp:{temp_dir}",
            "--no-part",
            "--hls-prefer-native",
            "--concurrent-fragments", "16",
            "--newline",
            "--no-warnings",
            "--merge-output-format", "mp4",
        ]

        # Embed subtitles directly via yt-dlp if we have a local sub file
        if sub_path and os.path.exists(sub_path):
            cmd.extend(["--embed-subs"])

        if shutil.which("aria2c"):
            cmd.extend([
                "--downloader", "aria2c",
                "--downloader-args", "aria2c:-x 16 -s 16 -k 1M",
            ])

        if task.get("headers"):
            headers = task["headers"]
            ua = headers.get("User-Agent") or headers.get("user-agent")
            if ua:
                cmd.extend(["--user-agent", ua])
            ref = headers.get("Referer") or headers.get("referer")
            if ref:
                cmd.extend(["--referer", ref])

        try:
            logger.info(f"Executing yt-dlp command: {' '.join(cmd)}")
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding="utf-8",
                errors="ignore",
            )

            last_save = 0
            while True:
                line = process.stdout.readline()
                if not line:
                    break
                self._parse_progress(task, line, last_save)
                # logger.debug(f"yt-dlp output: {line.strip()}") # verbose
                if time.time() - last_save > 3:
                    with self.lock:
                        self._save()
                    last_save = time.time()

            process.wait()
            logger.info(f"yt-dlp process finished with return code: {process.returncode}")

            process.wait()
            logger.info(f"yt-dlp process finished with return code: {process.returncode}")

            # Check if file exists even if return code is non-zero
            # (sometimes yt-dlp errors on minor things but file is okay)
            file_exists = False
            found_path = None
            
            # Helper to find file (similar logic to _organize_download)
            search_candidates = [mp4_out, os.path.join(temp_dir, os.path.basename(mp4_out))]
            
            # 1. Exact/Direct check
            for p in search_candidates:
                if os.path.exists(p) and os.path.getsize(p) > 1024: # meaningful size
                    file_exists = True
                    found_path = p
                    break
            
            # 2. Fuzzy check
            if not file_exists:
                base = os.path.splitext(os.path.basename(mp4_out))[0]
                for f in os.listdir(temp_dir):
                    if f.startswith(base) and f.lower().endswith((".mp4", ".mkv", ".webm")):
                        full_p = os.path.join(temp_dir, f)
                        if os.path.getsize(full_p) > 1024:
                            file_exists = True
                            found_path = full_p
                            break

            success = (process.returncode == 0) or file_exists

            with self.lock:
                if success:
                    task["status"] = "processing" # interim status
                    task["progress"] = 100
                    task["speed"] = ""
                    task["eta"] = "Finalizing..."
                    if found_path:
                        task["filename"] = found_path # Update to actual found path
                else:
                    task["status"] = "error"
                    task["error_msg"] = "yt-dlp failed (check logs)"
                self._save()

            # ── Step 3: Post-process ──────────────────────────
            if success:
                try:
                    self._organize_download(task, temp_dir)
                    logger.info("Organize download completed successfully")
                    with self.lock:
                        task["status"] = "completed"
                        task["eta"] = "Done"
                        self._save()
                except Exception as e:
                    logger.exception(f"Failed to organize download: {e}")
                    with self.lock:
                        task["status"] = "error"
                        task["error_msg"] = f"Move failed: {e}"
                        self._save()

                # Embed subtitles with ffmpeg if yt-dlp didn't do it
                try:
                    self._embed_subtitles(task, sub_path)
                except Exception as e:
                    logger.warning(f"Failed to embed subtitles: {e}")

        except Exception as e:
            logger.exception(f"Critical error in _process_task: {e}")
            with self.lock:
                task["status"] = "error"
                task["error_msg"] = str(e)
                self._save()

    # ──────────────────────────────────────────────────────────
    # Parse yt-dlp progress output
    # ──────────────────────────────────────────────────────────
    def _parse_progress(self, task, line, last_save):
        """Parse yt-dlp --newline output for progress info.

        Example lines:
          [download]   5.0% of  100.00MiB at  10.50MiB/s ETA 00:09
          [download]  45.2% of ~  50.00MiB at   2.30MiB/s ETA 00:18
          [download] 100% of  100.00MiB in 00:10
        """
        if "[download]" not in line or "%" not in line:
            return

        try:
            # Extract percentage
            pct_match = re.search(r"([\d.]+)%", line)
            if pct_match:
                percent = float(pct_match.group(1))
                with self.lock:
                    task["progress"] = min(percent, 100.0)

            # Extract total size: "of  100.00MiB" or "of ~  50.00MiB"
            size_match = re.search(r"of\s+~?\s*([\d.]+\s*[A-Za-z]+)", line)
            if size_match:
                with self.lock:
                    task["total_size"] = size_match.group(1).strip()

            # Extract speed: "at  10.50MiB/s"
            speed_match = re.search(r"at\s+([\d.]+\s*[A-Za-z/]+)", line)
            if speed_match:
                with self.lock:
                    task["speed"] = speed_match.group(1).strip()

            # Extract ETA: "ETA 00:09"
            eta_match = re.search(r"ETA\s+(\S+)", line)
            if eta_match:
                with self.lock:
                    task["eta"] = eta_match.group(1).strip()

            # Extract downloaded size from percentage + total
            if pct_match and size_match:
                try:
                    pct = float(pct_match.group(1))
                    total_str = size_match.group(1).strip()
                    total_val = float(re.search(r"[\d.]+", total_str).group())
                    unit = re.search(r"[A-Za-z]+", total_str).group()
                    downloaded = total_val * pct / 100.0
                    with self.lock:
                        task["downloaded_size"] = f"{downloaded:.1f}{unit}"
                except Exception:
                    pass

        except Exception:
            pass

    # ──────────────────────────────────────────────────────────
    # Download subtitle to temp dir
    # ──────────────────────────────────────────────────────────
    def _download_subtitle(self, task, temp_dir):
        """Download subtitle, returns path to local .srt/.vtt or None."""
        sub_path = None
        lang = self.settings.get("subtitle_language", "ar")

        # Try from provided subtitle list first
        if task.get("subtitles"):
            # Filter for specific language
            subs = [
                s for s in task["subtitles"]
                if s.get("lang", "").lower().startswith(lang.lower())
            ]
            if subs:
                try:
                    import requests
                    sub_url = subs[0]["url"]
                    base = sanitize_filename(task.get("title", "video"))
                    sub_ext = "vtt" if ".vtt" in sub_url else "srt"
                    sub_path = os.path.join(temp_dir, f"{base}.{sub_ext}")
                    r = requests.get(sub_url, timeout=15)
                    if r.status_code == 200 and r.content:
                        content = self._fix_subtitle_encoding(r.content)
                        with open(sub_path, "w", encoding="utf-8-sig") as f:
                            f.write(content)
                    else:
                        sub_path = None
                except Exception:
                    sub_path = None

        # Fallback: OpenSubtitles
        if not sub_path and task.get("meta"):
            try:
                from src.utils.subtitles import fetch_subtitle
                meta = task["meta"]
                if meta.get("type") == "movie" or (
                    meta.get("season") and meta.get("episode")
                ):
                    result = fetch_subtitle(
                        task["title"],
                        year=meta.get("year"),
                        season=meta.get("season"),
                        episode=meta.get("episode"),
                        language=lang
                    )
                    if result:
                        content_bytes, ext = result
                        base = sanitize_filename(task.get("title", "video"))
                        sub_path = os.path.join(temp_dir, f"{base}.{ext}")
                        decoded = self._fix_subtitle_encoding(content_bytes)
                        with open(sub_path, "w", encoding="utf-8-sig") as f:
                            f.write(decoded)
            except Exception:
                sub_path = None

        return sub_path

    def _fix_subtitle_encoding(self, content_bytes):
        """Try multiple encodings for Arabic subtitle content."""
        for enc in ["utf-8", "cp1256", "windows-1256", "iso-8859-6", "latin-1"]:
            try:
                return content_bytes.decode(enc)
            except (UnicodeDecodeError, AttributeError):
                continue
        # If already a string, return as-is
        if isinstance(content_bytes, str):
            return content_bytes
        return content_bytes.decode("utf-8", errors="ignore")

    # ──────────────────────────────────────────────────────────
    # Embed subtitles into MP4
    # ──────────────────────────────────────────────────────────
    def _embed_subtitles(self, task, sub_path=None):
        """Embed subtitle into MP4 as a soft-sub track using ffmpeg."""
        video_file = task.get("filename")
        if not video_file or not os.path.exists(video_file):
            return

        if not shutil.which("ffmpeg"):
            return

        # Find subtitle file: explicit path, or same-name in video dir
        if not sub_path or not os.path.exists(sub_path):
            base_name = os.path.splitext(video_file)[0]
            for ext in [".srt", ".vtt", ".ass", ".sub"]:
                candidate = base_name + ext
                if os.path.exists(candidate):
                    sub_path = candidate
                    break

        if not sub_path or not os.path.exists(sub_path):
            return

        temp_output = video_file + ".tmp.mp4"

        try:
            sub_ext = os.path.splitext(sub_path)[1].lower()
            sub_codec = "mov_text" if sub_ext in [".srt", ".vtt"] else "copy"

            cmd = [
                "ffmpeg",
                "-i", video_file,
                "-i", sub_path,
                "-c:v", "copy",
                "-c:a", "copy",
                "-c:s", sub_codec,
                "-metadata:s:s:0", "language=ara",
                "-disposition:s:0", "default",
                "-disposition:v:0", "default",
                "-y",
                "-loglevel", "error",
                temp_output,
            ]

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True,
            )
            _, stderr = process.communicate(timeout=300)

            if process.returncode == 0 and os.path.exists(temp_output):
                os.remove(video_file)
                shutil.move(temp_output, video_file)

                # Remove loose subtitle file
                try:
                    os.remove(sub_path)
                except Exception:
                    pass

                # Also remove any other loose subs with same base name
                base = os.path.splitext(video_file)[0]
                for ext in [".srt", ".vtt", ".ass", ".sub"]:
                    try:
                        p = base + ext
                        if os.path.exists(p):
                            os.remove(p)
                    except Exception:
                        pass
            else:
                if os.path.exists(temp_output):
                    os.remove(temp_output)
        except Exception:
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except Exception:
                    pass

    # ──────────────────────────────────────────────────────────
    # Organize downloaded file into folders
    # ──────────────────────────────────────────────────────────
    def _organize_download(self, task, temp_dir):
        """Move downloaded file to organized folder structure."""
        logger.info(f"Starting _organize_download for task {task['title']}")
        logger.debug(f"Temp dir: {temp_dir}")

        meta = task.get("meta") or {}
        title = task.get("title") or ""
        filename = task.get("filename") or ""
        logger.debug(f"Task filename: {filename}")

        # Find the actual downloaded file
        # ── Find the file ─────────────────────────────────────
        # yt-dlp might have merged formats, ending in .MKV or .MP4
        # We search in temp_dir for a file starting with base_name
        src_path = None
        
        # 1. Try exact match
        if os.path.exists(filename):
            src_path = filename
        
        # 2. Try in temp_dir with same name
        if not src_path:
             t_path = os.path.join(temp_dir, os.path.basename(filename))
             if os.path.exists(t_path):
                 src_path = t_path

        # 3. Fuzzy search in temp_dir by basename (ignoring extension)
        if not src_path:
            base = os.path.splitext(os.path.basename(filename))[0]
            logger.debug(f"Searching for base name: {base} in temp_dir")
            for f in os.listdir(temp_dir):
                if f.startswith(base) and f.lower().endswith((".mp4", ".mkv", ".webm")):
                    src_path = os.path.join(temp_dir, f)
                    logger.info(f"Fuzzy match found: {src_path}")
                    break

        if not src_path:
            console.print(f"[red]Could not find downloaded file for: {title}[/red]")
            console.print(f"[dim]Expected: {filename}[/dim]")
            logger.error(f"Could not find downloaded file. Expected basename of {filename} in {temp_dir}")
            raise FileNotFoundError(f"Downloaded file not found for {title}")
        
        logger.info(f"Source file found: {src_path}")

        # Default to ~/Downloads/Cinema-CLI if no setting is provided
        default_dl = os.path.join(os.path.expanduser("~"), "Downloads", "Cinema-CLI")
        downloads_root = self.settings.get("download_path") or default_dl
        logger.debug(f"Target download root: {downloads_root}")

        media_type = meta.get("type") if meta else None
        if not media_type:
            if re.search(r"S\d{1,2}E\d{1,2}", title):
                media_type = "tv"
            else:
                media_type = "movie"

        if media_type == "tv":
            m = re.match(r"^(.*?)\sS\d{1,2}E\d{1,2}", title)
            series = m.group(1) if m else title.split(" - ")[0] or "Series"
            season = meta.get("season") or 0
            dest_dir = os.path.join(
                downloads_root, "tv",
                sanitize_filename(series),
                f"Season {int(season):02d}",
            )
        else:
            movie_name = title.split(" - ")[0] or "Movie"
            dest_dir = os.path.join(
                downloads_root, "movies", sanitize_filename(movie_name)
            )

        os.makedirs(dest_dir, exist_ok=True)
        dest_path = os.path.join(dest_dir, os.path.basename(src_path))
        logger.info(f"Destination path: {dest_path}")
        
        if os.path.exists(dest_path):
            # Rename if exists
            base, ext = os.path.splitext(dest_path)
            dest_path = f"{base}_{int(time.time())}{ext}"
            logger.info(f"File exists, renaming to: {dest_path}")

        try:
            # Handle cross-device move
            shutil.move(src_path, dest_path)
            logger.info("File move successful")

            # Move any remaining subtitle files alongside the video
            base_name = os.path.splitext(os.path.basename(src_path))[0]
            for ext in [".srt", ".vtt", ".ass", ".sub"]:
                possible = os.path.join(os.path.dirname(src_path), base_name + ext)
                if os.path.exists(possible):
                    try:
                        dest_sub = os.path.join(dest_dir, os.path.basename(possible))
                        if os.path.exists(dest_sub):
                             base_s, ext_s = os.path.splitext(dest_sub)
                             dest_sub = f"{base_s}_{int(time.time())}{ext_s}"
                        shutil.move(possible, dest_sub)
                        logger.debug(f"Moved subtitle {possible}")
                    except Exception as e:
                        logger.warning(f"Failed to move subtitle {possible}: {e}")

            with self.lock:
                task["filename"] = dest_path
                self._save()
        except Exception as e:
            console.print(f"[red]Failed to move file to {dest_path}: {e}[/red]")
            logger.exception(f"Failed to move file to {dest_path}")
            raise e
