# import os
# import shutil
# import subprocess
# import threading
# import time
# import winsound
# from rich.panel import Panel
# from src.config import console, SUCCESS

# def show_notification(title, message):
#     """Show a Windows Balloon Tip notification"""
#     try:
#         ps_script = f"""
#         [void] [System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")
#         $objNotifyIcon = New-Object System.Windows.Forms.NotifyIcon
#         $objNotifyIcon.Icon = [System.Drawing.SystemIcons]::Information
#         $objNotifyIcon.Visible = $True
#         $objNotifyIcon.BalloonTipIcon = "Info"
#         $objNotifyIcon.BalloonTipTitle = "{title}"
#         $objNotifyIcon.BalloonTipText = "{message}"
#         $objNotifyIcon.ShowBalloonTip(10000)
#         start-sleep -s 5
#         $objNotifyIcon.Dispose()
#         """
#         subprocess.Popen(["powershell", "-Command", ps_script], creationflags=subprocess.CREATE_NO_WINDOW)
#     except:
#         pass

# def download_stream(url, filename, subtitles=None, headers=None, session=None):
#     def run_download():
#         success = False
#         # Create a hidden temp directory for fragments
#         temp_dir = os.path.join(os.getcwd(), ".download_temp")
#         os.makedirs(temp_dir, exist_ok=True)
        
#         # Download subtitle if available
#         if subtitles:
#             ar = [s for s in subtitles if s.get("lang", "").lower() in ["arabic", "ar", "ara"]]
#             if ar:
#                 sub_url = ar[0]['url']
#                 base, _ = os.path.splitext(filename)
#                 sub_ext = 'srt'
#                 if '.vtt' in sub_url:
#                     sub_ext = 'vtt'
                
#                 sub_filename = f"{base}.{sub_ext}"
#                 try:
#                     import requests
#                     downloader = session if session else requests
#                     r = downloader.get(sub_url, timeout=15)
#                     if r.status_code == 200:
#                         with open(sub_filename, 'wb') as f:
#                             f.write(r.content)
#                 except:
#                     pass

#         try:
#             if '.m3u8' in url or '.m3u' in url:
#                 base, _ = os.path.splitext(filename)
#                 mp4_out = base + ".mp4"

#                 # 1. Try yt-dlp (Best for speed and concurrency)
#                 if shutil.which("yt-dlp"):
#                     # Use quiet mode and no warnings to suppress logs
#                     # Increase concurrency to 16 for maximum speed
#                     # -P "temp:..." directs intermediate files to the temp folder
#                     cmd = [
#                         "yt-dlp", url, 
#                         "-o", mp4_out, 
#                         "-P", f"temp:{temp_dir}",
#                         "--no-part", 
#                         "--hls-prefer-native", 
#                         "--concurrent-fragments", "16", 
#                         "--quiet", "--no-warnings"
#                     ]
                    
#                     # If aria2c is available, use it as the external downloader for even better speed/stability
#                     if shutil.which("aria2c"):
#                         cmd.extend(["--downloader", "aria2c", "--downloader-args", "aria2c:-x 16 -s 16 -k 1M"])

#                     if headers:
#                         ua = headers.get('User-Agent') or headers.get('user-agent')
#                         if ua: cmd.extend(["--user-agent", ua])
#                         ref = headers.get('Referer') or headers.get('referer')
#                         if ref: cmd.extend(["--referer", ref])
                    
#                     subprocess.run(cmd, check=True)
#                     success = True
                    
#                 # 2. Try FFmpeg (Standard, faster than recording)
#                 elif shutil.which("ffmpeg"):
#                     cmd = ["ffmpeg", "-y"]
#                     if headers:
#                         ua = headers.get('User-Agent') or headers.get('user-agent')
#                         if ua: cmd.extend(["-user_agent", ua])
#                         ref = headers.get('Referer') or headers.get('referer')
#                         if ref: cmd.extend(["-headers", f"Referer: {ref}"])
                    
#                     cmd.extend(["-i", url, "-c", "copy", "-bsf:a", "aac_adtstoasc", "-loglevel", "quiet", mp4_out])
#                     subprocess.run(cmd, check=True)
#                     success = True

#                 # 3. Fallback to MPV (Slow recording) - blocking not ideal for bg, but ok if threaded
#                 else:
#                     mpv_cmd = ["mpv", url, f"--stream-record={filename}", "--vo=null", "--ao=null", "--msg-level=all=no"]
#                     if headers:
#                         ua = headers.get('User-Agent') or headers.get('user-agent')
#                         if ua: mpv_cmd.append(f"--user-agent={ua}")
#                         ref = headers.get('Referer') or headers.get('referer')
#                         if ref: mpv_cmd.append(f"--referrer={ref}")
#                     subprocess.run(mpv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
#                     success = True

#             # Direct HTTP download
#             elif shutil.which("aria2c"):
#                 aria_cmd = [
#                     "aria2c", url, 
#                     "-o", filename, 
#                     "-d", os.getcwd(),
#                     "-x", "16", "-s", "16", 
#                     "--file-allocation=none", 
#                     "--summary-interval=1", 
#                     "--quiet=true"
#                 ]
#                 if headers:
#                     ua = headers.get('User-Agent') or headers.get('user-agent')
#                     if ua: aria_cmd.append(f"--user-agent={ua}")
#                     ref = headers.get('Referer') or headers.get('referer')
#                     if ref: aria_cmd.append(f"--referer={ref}")
#                 subprocess.run(aria_cmd, check=True)
#                 success = True
            
#             # Standard requests download
#             elif session:
#                 req_headers = {}
#                 if headers:
#                     for k, v in headers.items():
#                         if ',' not in v:
#                             req_headers[k] = v
#                 if 'User-Agent' not in req_headers:
#                     req_headers['User-Agent'] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                
#                 with session.get(url, headers=req_headers, stream=True, timeout=(10, 300)) as r:
#                     r.raise_for_status()
#                     with open(filename, 'wb') as f:
#                         for chunk in r.iter_content(chunk_size=8192):
#                             f.write(chunk)
#                 success = True

#         except Exception as e:
#             show_notification("Download Failed", f"Error downloading {filename}")
#             return

#         if success:
#             # Play sound
#             try: winsound.MessageBeep(winsound.MB_ICONASTERISK)
#             except: pass
#             # Show Notification
#             show_notification("Download Complete", f"{filename} is ready to watch!")
            
#             # Try to clean up temp dir if empty
#             try: os.rmdir(temp_dir)
#             except: pass

#     # Start download in background thread
#     t = threading.Thread(target=run_download, daemon=True)
#     t.start()
    
#     console.print(Panel(f"[green]Download started in background![/green]\n[dim]You can continue using the app while it downloads.[/dim]", title="Download Started", border_style=SUCCESS))
#     time.sleep(1.5)


import os
import shutil
import subprocess
import threading
import time
import winsound
from rich.panel import Panel
from src.config import console, SUCCESS
from src.utils.subtitles import fetch_arabic_subtitle

def show_notification(title, message):
    """Show a Windows Balloon Tip notification"""
    # This function is platform-specific (Windows) and will be left as is.
    try:
        ps_script = f"""
        [void] [System.Reflection.Assembly]::LoadWithPartialName("System.Windows.Forms")
        $objNotifyIcon = New-Object System.Windows.Forms.NotifyIcon
        $objNotifyIcon.Icon = [System.Drawing.SystemIcons]::Information
        $objNotifyIcon.Visible = $True
        $objNotifyIcon.BalloonTipIcon = "Info"
        $objNotifyIcon.BalloonTipTitle = "{title}"
        $objNotifyIcon.BalloonTipText = "{message}"
        $objNotifyIcon.ShowBalloonTip(10000)
        start-sleep -s 5
        $objNotifyIcon.Dispose()
        """
        subprocess.Popen(["powershell", "-Command", ps_script], creationflags=subprocess.CREATE_NO_WINDOW)
    except:
        pass

def download_stream(url, filename, subtitles=None, headers=None, session=None, ffmpeg_on_fail=True, embed_subtitles=True, meta=None):
    def run_download():
        success = False
        # Create a hidden temp directory for fragments
        temp_dir = os.path.join(os.getcwd(), ".download_temp")
        os.makedirs(temp_dir, exist_ok=True)
        mp4_out = None
        
        sub_filename = None
        if subtitles:
            ar = [s for s in subtitles if s.get("lang", "").lower() in ["arabic", "ar", "ara"]]
            if ar:
                sub_url = ar[0]['url']
                base, _ = os.path.splitext(filename)
                sub_ext = 'srt'
                if '.vtt' in sub_url:
                    sub_ext = 'vtt'
                sub_filename = os.path.join(temp_dir, f"subtitle.{sub_ext}")
                try:
                    import requests
                    downloader = session if session else requests
                    r = downloader.get(sub_url, timeout=15)
                    if r.status_code == 200:
                        with open(sub_filename, 'wb') as f:
                            f.write(r.content)
                except:
                    sub_filename = None
        if not sub_filename:
            try:
                base, _ = os.path.splitext(filename)
                q = base.replace("_", " ").strip()
                yr = None
                sn = None
                epn = None
                if isinstance(meta, dict):
                    yr = meta.get("year")
                    sn = meta.get("season")
                    epn = meta.get("episode")
                res = fetch_arabic_subtitle(q, year=yr, season=sn, episode=epn)
                if res:
                    content, sub_ext = res
                    sub_filename = os.path.join(temp_dir, f"subtitle.{sub_ext}")
                    with open(sub_filename, "wb") as f:
                        f.write(content)
            except:
                sub_filename = None

        try:
            # --- START MODIFICATION ---
            # Always use yt-dlp for HLS/M3U8 streams and direct links if available, 
            # as it handles headers, retries, and merging better.
            if shutil.which("yt-dlp"):
                # Determine output filename: force .mp4 for HLS/M3U8 streams for better compatibility
                base, ext = os.path.splitext(filename)
                if '.m3u8' in url or '.m3u' in url:
                    mp4_out = base + ".mp4"
                else:
                    mp4_out = filename

                cmd = [
                    "yt-dlp", url,
                    "-o", mp4_out,
                    "-P", f"temp:{temp_dir}",
                    "--no-part",
                    # Prefer ffmpeg for HLS when available (more robust for some streams)
                    "--hls-prefer-ffmpeg",
                    # Reduce concurrent fragments to avoid overwhelming servers and hitting timeouts
                    "--concurrent-fragments", "4",
                    # Retries and socket timeout to better handle transient network issues
                    "--fragment-retries", "10",
                    "--retries", "5",
                    "--socket-timeout", "60",
                    "--quiet", "--no-warnings"
                ]

                # If aria2c is available, prepare to use it as the external downloader (args set per-attempt)
                if headers:
                    # yt-dlp uses --add-header for custom headers
                    for key, value in headers.items():
                        # yt-dlp can't handle headers with commas in values, so we skip them
                        if ',' not in str(value):
                            cmd.extend(["--add-header", f"{key}:{value}"])

                # Attempt download with fallback concurrency levels: try high concurrency first,
                # then retry with progressively lower concurrency on failure.
                conc_levels = [16, 8, 4, 1]
                last_exc = None
                for conc in conc_levels:
                    attempt_cmd = cmd.copy()

                    # Ensure --concurrent-fragments is set to current concurrency
                    if "--concurrent-fragments" in attempt_cmd:
                        idx = attempt_cmd.index("--concurrent-fragments")
                        attempt_cmd[idx+1] = str(conc)
                    else:
                        attempt_cmd.extend(["--concurrent-fragments", str(conc)])

                    # Prepare aria2c downloader args according to concurrency (if aria2c present)
                    # Remove any existing downloader args from attempt_cmd
                    if "--downloader" in attempt_cmd:
                        try:
                            di = attempt_cmd.index("--downloader")
                            # remove '--downloader' and the next value
                            del attempt_cmd[di:di+2]
                        except Exception:
                            pass
                    if "--downloader-args" in attempt_cmd:
                        try:
                            da = attempt_cmd.index("--downloader-args")
                            # remove '--downloader-args' and the next value
                            del attempt_cmd[da:da+2]
                        except Exception:
                            pass

                    if shutil.which("aria2c"):
                        aria_x = max(1, min(conc, 8))
                        aria_s = max(1, min(conc, 8))
                        aria_args = f"aria2c:-x {aria_x} -s {aria_s} -k 1M --timeout=60 --connect-timeout=10 --max-tries=5 --retry-wait=5"
                        attempt_cmd.extend(["--downloader", "aria2c", "--downloader-args", aria_args])

                    try:
                        subprocess.run(attempt_cmd, check=True)
                        success = True
                        break
                    except Exception as e:
                        last_exc = e
                        # small backoff before retrying
                        time.sleep(1)

                if not success and last_exc:
                    # If configured, try ffmpeg fallback before giving up
                    if ffmpeg_on_fail and shutil.which("ffmpeg") and ('.m3u8' in url or '.m3u' in url):
                        try:
                            base, _ = os.path.splitext(filename)
                            mp4_out = base + ".mp4"
                            ff_cmd = ["ffmpeg", "-y"]
                            if headers:
                                ua = headers.get('User-Agent') or headers.get('user-agent')
                                if ua: ff_cmd.extend(["-user_agent", ua])
                                ref = headers.get('Referer') or headers.get('referer')
                                if ref: ff_cmd.extend(["-headers", f"Referer: {ref}"])

                            ff_cmd.extend(["-i", url, "-c", "copy", "-bsf:a", "aac_adtstoasc", "-loglevel", "quiet", mp4_out])
                            subprocess.run(ff_cmd, check=True)
                            success = True
                        except Exception as e:
                            # keep the last exception to surface below
                            last_exc = e
                    if not success:
                        # raise to be caught by outer except and notify user
                        raise last_exc
            # --- END MODIFICATION ---
            
            # 2. Try FFmpeg (Standard, faster than recording) - only if yt-dlp is not found
            elif shutil.which("ffmpeg") and ('.m3u8' in url or '.m3u' in url):
                base, _ = os.path.splitext(filename)
                mp4_out = base + ".mp4"
                cmd = ["ffmpeg", "-y"]
                if headers:
                    ua = headers.get('User-Agent') or headers.get('user-agent')
                    if ua: cmd.extend(["-user_agent", ua])
                    ref = headers.get('Referer') or headers.get('referer')
                    if ref: cmd.extend(["-headers", f"Referer: {ref}"])
                
                cmd.extend(["-i", url, "-c", "copy", "-bsf:a", "aac_adtstoasc", "-loglevel", "quiet", mp4_out])
                subprocess.run(cmd, check=True)
                success = True

            # 3. Fallback to MPV (Slow recording) - blocking not ideal for bg, but ok if threaded
            elif ('.m3u8' in url or '.m3u' in url) and shutil.which("mpv"):
                mpv_cmd = ["mpv", url, f"--stream-record={filename}", "--vo=null", "--ao=null", "--msg-level=all=no"]
                if headers:
                    ua = headers.get('User-Agent') or headers.get('user-agent')
                    if ua: mpv_cmd.append(f"--user-agent={ua}")
                    ref = headers.get('Referer') or headers.get('referer')
                    if ref: mpv_cmd.append(f"--referrer={ref}")
                subprocess.run(mpv_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                success = True

            # Direct HTTP download with aria2c - only if yt-dlp is not found
            elif shutil.which("aria2c"):
                aria_cmd = [
                    "aria2c", url,
                    "-o", filename,
                    "-d", os.getcwd(),
                    "-x", "8", "-s", "8",
                    "--file-allocation=none",
                    "--summary-interval=1",
                    "--timeout=60",
                    "--connect-timeout=10",
                    "--max-tries=5",
                    "--retry-wait=5",
                    "--quiet=true"
                ]
                if headers:
                    ua = headers.get('User-Agent') or headers.get('user-agent')
                    if ua: aria_cmd.append(f"--user-agent={ua}")
                    ref = headers.get('Referer') or headers.get('referer')
                    if ref: aria_cmd.append(f"--referer={ref}")
                subprocess.run(aria_cmd, check=True)
                success = True
            
            # Standard requests download - only if all other tools are not found
            elif session:
                req_headers = {}
                if headers:
                    for k, v in headers.items():
                        if ',' not in v:
                            req_headers[k] = v
                if 'User-Agent' not in req_headers:
                    req_headers['User-Agent'] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                
                with session.get(url, headers=req_headers, stream=True, timeout=(10, 300)) as r:
                    r.raise_for_status()
                    with open(filename, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                success = True
            else:
                console.print("[red]No suitable download tool (yt-dlp, ffmpeg, aria2c) found.[/red]")
                return

        except Exception as e:
            console.print(f"[red]Download failed for {filename}: {e}[/red]")
            show_notification("Download Failed", f"Error downloading {filename}")
            return

        if success:
            # Determine final video path
            final_video = None
            if mp4_out:
                final_video = mp4_out
            else:
                final_video = filename

            # If we have downloaded a subtitle and embedding is requested, attempt to mux it into the mp4
            if embed_subtitles and shutil.which("ffmpeg") and ('sub_filename' in locals() and os.path.exists(sub_filename)):
                try:
                    sub_path = sub_filename
                    merged = os.path.join(temp_dir, "merged_output.mp4")
                    # Use mov_text for MP4 soft subtitles (widely supported)
                    ff_merge_cmd = ["ffmpeg", "-y", "-i", final_video, "-i", sub_path, "-c", "copy", "-c:s", "mov_text", merged]
                    subprocess.run(ff_merge_cmd, check=True)
                    # Replace final video with merged file
                    try:
                        os.replace(merged, final_video)
                    except Exception:
                        # fallback to copy
                        shutil.copyfile(merged, final_video)
                    # remove subtitle file
                    try:
                        os.remove(sub_path)
                    except Exception:
                        pass
                except Exception:
                    # ignore subtitle merge errors; video is still available
                    pass

            # Play sound
            try: winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except: pass
            # Show Notification
            show_notification("Download Complete", f"{filename} is ready to watch!")
            
            # Try to clean up temp dir if empty
            try: shutil.rmtree(temp_dir)
            except: pass

    # Start download in background thread
    t = threading.Thread(target=run_download, daemon=True)
    t.start()
    
    console.print(Panel(f"[green]Download started in background![/green]\n[dim]You can continue using the app while it downloads.[/dim]", title="Download Started", border_style=SUCCESS))
    time.sleep(1.5)
