import os
import json
import threading
import time
import subprocess
import shutil
import re
from src.config import console, SUCCESS, WARNING, TEXT
from src.utils.storage import load_json_data, save_json_data
from src.utils.utils import sanitize_filename

DOWNLOADS_FILE = os.path.expanduser("~/.cinema-cli-downloads.json")

class DownloadManager:
    def __init__(self):
        self.queue = load_json_data(DOWNLOADS_FILE) or []
        # Reset any 'downloading' status to 'pending' on startup
        for task in self.queue:
            if task['status'] == 'downloading':
                task['status'] = 'pending'
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
            "speed": "0 B/s",
            "eta": "00:00",
            "added_at": time.time()
        }
        with self.lock:
            self.queue.append(task)
            self._save()
        console.print(f"[green]Added to download queue: {filename}[/green]")
        time.sleep(1)
        
    def remove_task(self, task_id):
        with self.lock:
            self.queue = [t for t in self.queue if t['id'] != task_id]
            self._save()

    def retry_task(self, task_id):
        with self.lock:
            for t in self.queue:
                if t['id'] == task_id:
                    t['status'] = 'pending'
                    t['progress'] = 0
            self._save()
        
    def _save(self):
        save_json_data(DOWNLOADS_FILE, self.queue)
        
    def _worker(self):
        while self.running:
            task = None
            with self.lock:
                pending = [t for t in self.queue if t['status'] == 'pending']
                if pending:
                    task = pending[0]
            
            if task:
                self._process_task(task)
            else:
                time.sleep(1)

    def _process_task(self, task):
        with self.lock:
            task['status'] = 'downloading'
            self._save()

        # Create temp dir
        temp_dir = os.path.join(os.getcwd(), ".download_temp")
        os.makedirs(temp_dir, exist_ok=True)
        
        # Download subtitle (logic from downloads.py)
        sub_downloaded = False
        if task.get('subtitles'):
            ar = [s for s in task['subtitles'] if s.get("lang", "").lower() in ["arabic", "ar", "ara"]]
            if ar:
                try:
                    import requests
                    sub_url = ar[0]['url']
                    base, _ = os.path.splitext(task['filename'])
                    sub_ext = 'vtt' if '.vtt' in sub_url else 'srt'
                    sub_filename = f"{base}.{sub_ext}"
                    r = requests.get(sub_url, timeout=15)
                    if r.status_code == 200:
                        content = r.content
                        # Fix encoding: try UTF-8 first, then CP1256 (Arabic), fallback to latin-1
                        # We re-encode to UTF-8-SIG (with BOM) to help players on Windows detect it correctly
                        decoded = None
                        for enc in ['utf-8', 'cp1256', 'windows-1256', 'iso-8859-6', 'latin-1']:
                            try:
                                decoded = content.decode(enc)
                                break
                            except UnicodeDecodeError:
                                continue
                        
                        if decoded:
                            with open(sub_filename, 'w', encoding='utf-8-sig') as f:
                                f.write(decoded)
                        else:
                            # Fallback: just write bytes
                            with open(sub_filename, 'wb') as f:
                                f.write(content)
                        sub_downloaded = True
                except:
                    pass

        # Fallback: OpenSubtitles if no Arabic subtitle found
        if not sub_downloaded and task.get('meta'):
            try:
                meta = task['meta']
                # Only try if we have enough info
                if meta.get('type') == 'movie' or (meta.get('season') and meta.get('episode')):
                    # Reconstruct title or pass None to let fetch_arabic_subtitle use only IDs if it supported it, 
                    # but it uses query + year/season/episode.
                    # We'll use the task title or meta title if available.
                    # task['title'] is "Show S01E01 - ..." which is good for query.
                    
                    # But fetch_arabic_subtitle takes (title, year, season, episode).
                    # Ideally "title" should be the show name or movie name.
                    # task['title'] is the full display string.
                    # We might need to extract the show name from meta if possible, or just use task['title'] as query.
                    
                    # Let's check what meta contains. 
                    # TV: {'year': '...', 'season': 1, 'episode': 1, 'tmdb_id': ..., 'type': 'tv'}
                    # Movie: {'year': '...', 'tmdb_id': ..., 'type': 'movie'}
                    # It doesn't seem to have the raw show/movie name.
                    # However, fetch_arabic_subtitle uses the 'query' param.
                    
                    search_query = task['title']
                    # Try to clean up "S01E01 - ..." for TV shows to get better results?
                    # Actually, OpenSubtitles API is smart, but cleaner is better.
                    # If we don't have the show name, using the full string is the best bet.
                    
                    result = fetch_arabic_subtitle(
                        search_query, 
                        year=meta.get('year'), 
                        season=meta.get('season'), 
                        episode=meta.get('episode')
                    )
                    
                    if result:
                        content, ext = result
                        base, _ = os.path.splitext(task['filename'])
                        sub_filename = f"{base}.{ext}"
                        
                        # Apply encoding fix here too just in case
                        decoded = None
                        for enc in ['utf-8', 'cp1256', 'windows-1256', 'iso-8859-6', 'latin-1']:
                            try:
                                decoded = content.decode(enc)
                                break
                            except UnicodeDecodeError:
                                continue
                        
                        if decoded:
                            with open(sub_filename, 'w', encoding='utf-8-sig') as f:
                                f.write(decoded)
                        else:
                            with open(sub_filename, 'wb') as f:
                                f.write(content)
            except:
                pass

        # Prepare yt-dlp command
        url = task['url']
        mp4_out = task['filename']
        
        cmd = [
            "yt-dlp", url, 
            "-o", mp4_out,
            "-P", f"temp:{temp_dir}",
            "--no-part", 
            "--hls-prefer-native", 
            "--concurrent-fragments", "16",
            "--newline", # Important for parsing progress
            "--no-warnings"
        ]
        
        if shutil.which("aria2c"):
            cmd.extend(["--downloader", "aria2c", "--downloader-args", "aria2c:-x 16 -s 16 -k 1M"])

        if task.get('headers'):
            headers = task['headers']
            ua = headers.get('User-Agent') or headers.get('user-agent')
            if ua: cmd.extend(["--user-agent", ua])
            ref = headers.get('Referer') or headers.get('referer')
            if ref: cmd.extend(["--referer", ref])

        try:
            process = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.STDOUT, 
                universal_newlines=True,
                encoding='utf-8' # Ensure encoding is set
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
                        percent_str = line.split('%')[0].split()[-1]
                        percent = float(percent_str)
                        
                        # Extract other info if possible
                        speed = "Unknown"
                        if "at" in line:
                            parts = line.split("at")
                            if len(parts) > 1:
                                speed = parts[1].split("ETA")[0].strip()
                        
                        with self.lock:
                            task['progress'] = percent
                            task['speed'] = speed
                            # Save every 5 seconds or if finished
                            if time.time() - last_save > 5:
                                self._save()
                                last_save = time.time()
                    except:
                        pass
            
            process.wait()
            
            with self.lock:
                if process.returncode == 0:
                    task['status'] = 'completed'
                    task['progress'] = 100
                else:
                    task['status'] = 'error'
                self._save()

            # Organize completed downloads into folders (series/season for TV)
            if process.returncode == 0:
                try:
                    # Do organization outside the lock to avoid long lock hold
                    self._organize_download(task, temp_dir)
                    
                    # Embed subtitles into the video file
                    try:
                        self._embed_subtitles(task)
                    except Exception as e:
                        console.print(f"[yellow]Could not embed subtitles: {e}[/yellow]")
                except Exception:
                    pass
                
        except Exception as e:
            with self.lock:
                task['status'] = 'error'
                self._save()

    def get_queue(self):
        with self.lock:
            return list(self.queue)

    def _organize_download(self, task, temp_dir):
        """Auto-create series/season directories and move downloaded files there.
        Structure: downloads/tv/SeriesName/Season XX/ or downloads/movies/MovieName/
        """
        meta = task.get('meta') or {}
        title = task.get('title') or ''
        filename = task.get('filename') or ''

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

        # Determine destination folder
        downloads_root = os.path.join(os.getcwd(), 'downloads')
        
        # Use meta.type if available, otherwise infer from title pattern
        media_type = meta.get('type') if meta else None
        if not media_type:
            # Infer from title pattern (e.g., "Show Name S01E01")
            import re
            if re.search(r'S\d{1,2}E\d{1,2}', title):
                media_type = 'tv'
            else:
                media_type = 'movie'

        if media_type == 'tv':
            import re
            m = re.match(r'^(.*?)\sS\d{1,2}E\d{1,2}', title)
            if m:
                series = m.group(1)
            else:
                series = title.split(' - ')[0] if title else 'Series'

            season = meta.get('season') if meta else 0
            season = season or 0
            dest_dir = os.path.join(downloads_root, 'tv', sanitize_filename(series), f"Season {int(season):02d}")
        else:
            movie_name = title.split(' - ')[0] if title else 'Movie'
            dest_dir = os.path.join(downloads_root, 'movies', sanitize_filename(movie_name))

        os.makedirs(dest_dir, exist_ok=True)

        dest_path = os.path.join(dest_dir, os.path.basename(file_path))
        try:
            shutil.move(file_path, dest_path)

            # Move subtitle files with same base name to the same folder as the video
            for ext in ['.srt', '.vtt', '.ass', '.sub']:
                possible = os.path.join(os.path.dirname(file_path), base_name + ext)
                if os.path.exists(possible):
                    try:
                        shutil.move(possible, os.path.join(dest_dir, os.path.basename(possible)))
                    except Exception:
                        pass

            # Update task filename in queue
            with self.lock:
                task['filename'] = dest_path
                self._save()

            console.print(f"[green]Moved to {dest_dir}[/green]")
        except Exception as e:
            console.print(f"[yellow]Could not organize file: {e}[/yellow]")

    def _embed_subtitles(self, task):
        """Embed subtitle file into MP4 using ffmpeg with proper metadata."""
        video_file = task.get('filename')
        
        if not video_file or not os.path.exists(video_file):
            return
        
        # Check if ffmpeg is available
        if not shutil.which("ffmpeg"):
            return
        
        base_name = os.path.splitext(video_file)[0]
        sub_file = None
        
        # Find subtitle file with same base name
        for ext in ['.srt', '.vtt', '.ass', '.sub']:
            candidate = base_name + ext
            if os.path.exists(candidate):
                sub_file = candidate
                break
        
        if not sub_file:
            return
        
        # Create temp output file
        temp_output = video_file + ".tmp.mp4"
        
        try:
            # Determine subtitle codec based on extension
            sub_ext = os.path.splitext(sub_file)[1].lower()
            sub_codec = 'mov_text' if sub_ext in ['.srt', '.vtt'] else 'copy'
            
            # ffmpeg command to embed subtitle with proper disposition
            cmd = [
                "ffmpeg",
                "-i", video_file,
                "-i", sub_file,
                "-c:v", "copy",
                "-c:a", "copy",
                "-c:s", sub_codec,
                "-metadata:s:s:0", "language=ara",      # Mark as Arabic
                "-disposition:s:0", "default",           # Make subtitle default/visible
                "-disposition:v:0", "default",           # Keep video as default
                "-y",
                "-loglevel", "error",
                temp_output
            ]
            
            # Run ffmpeg silently
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            stdout, stderr = process.communicate()
            
            if process.returncode == 0:
                # Replace original with embedded version
                os.remove(video_file)
                shutil.move(temp_output, video_file)
                
                # Delete the subtitle file since it's now embedded
                try:
                    os.remove(sub_file)
                except Exception:
                    pass
                
                console.print(f"[green]Subtitles embedded into video[/green]")
            else:
                # Clean up temp file on failure
                if os.path.exists(temp_output):
                    os.remove(temp_output)
                console.print(f"[yellow]ffmpeg error: {stderr[:200]}[/yellow]")
        except Exception as e:
            console.print(f"[yellow]Could not embed subtitles: {e}[/yellow]")
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except Exception:
                    pass

