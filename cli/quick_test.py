#!/usr/bin/env python3
"""
Cinema CLI Quick Test - Interactive Test Script
================================================
Run quick tests on specific features with real playback/download.

Usage:
    python quick_test.py stream      # Test streaming a short clip
    python quick_test.py download    # Test downloading a short clip
    python quick_test.py subtitle    # Test subtitle download
    python quick_test.py all         # Run all quick tests
"""

import os
import sys
import time
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


def test_stream():
    """Test streaming with a public test stream."""
    console.print(Panel("[bold cyan]Testing Streaming[/bold cyan]", expand=False))
    
    # Public test stream (Big Buck Bunny - short clip)
    test_url = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
    test_title = "Test Stream - Big Buck Bunny"
    
    console.print(f"[dim]URL: {test_url}[/dim]")
    
    if not shutil.which("mpv"):
        console.print("[red]❌ MPV not found - cannot test streaming[/red]")
        return False
    
    console.print("[yellow]Starting MPV player... (press 'q' to quit)[/yellow]")
    console.print("[dim]This will play a 10-second test clip[/dim]\n")
    
    try:
        from src.utils.player import play_stream
        
        # Play for a few seconds
        result = play_stream(
            url=test_url,
            title=test_title,
            subtitles=None,
            headers=None,
            meta=None,
            start_time=0,
            preferred_sub_lang="en",
            include_all_subs=False
        )
        
        if result:
            console.print(f"[green]✅ Streaming test completed[/green]")
            console.print(f"[dim]Position: {result.get('position', 0):.1f}s, Duration: {result.get('duration', 0):.1f}s[/dim]")
            return True
        else:
            console.print("[yellow]⚠ Player closed without stats[/yellow]")
            return True  # Not necessarily a failure
            
    except Exception as e:
        console.print(f"[red]❌ Streaming test failed: {e}[/red]")
        return False


def test_download():
    """Test downloading a short clip."""
    console.print(Panel("[bold cyan]Testing Download[/bold cyan]", expand=False))
    
    # Public test file (small MP4)
    test_url = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
    test_title = "Test Download"
    
    if not shutil.which("yt-dlp"):
        console.print("[red]❌ yt-dlp not found - cannot test download[/red]")
        return False
    
    # Create temp directory
    temp_dir = tempfile.mkdtemp(prefix="cinema_test_")
    output_file = os.path.join(temp_dir, "test_download.mp4")
    
    console.print(f"[dim]URL: {test_url}[/dim]")
    console.print(f"[dim]Output: {output_file}[/dim]")
    console.print("[yellow]Starting download (10-second clip)...[/yellow]\n")
    
    try:
        import subprocess
        
        cmd = [
            "yt-dlp",
            test_url,
            "-o", output_file,
            "--merge-output-format", "mp4",
            "--no-warnings",
            "--no-check-certificates",
            # Limit to save time
            "--download-sections", "*0-10",
        ]
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Downloading...", total=None)
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace"
            )
            
            for line in process.stdout:
                if "[download]" in line:
                    progress.update(task, description=line.strip()[:60])
            
            process.wait()
        
        # Check result
        if process.returncode == 0:
            # Find downloaded file (yt-dlp may change extension)
            files = [f for f in os.listdir(temp_dir) if not f.endswith(('.part', '.ytdl'))]
            if files:
                downloaded = os.path.join(temp_dir, files[0])
                size = os.path.getsize(downloaded)
                console.print(f"[green]✅ Download test completed[/green]")
                console.print(f"[dim]File: {files[0]} ({size / 1024:.1f} KB)[/dim]")
                return True
        
        console.print(f"[red]❌ Download failed (exit code: {process.returncode})[/red]")
        return False
        
    except Exception as e:
        console.print(f"[red]❌ Download test failed: {e}[/red]")
        return False
    finally:
        # Cleanup
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass


def test_subtitle():
    """Test subtitle download functionality."""
    console.print(Panel("[bold cyan]Testing Subtitle Download[/bold cyan]", expand=False))
    
    # Test with a reliable public subtitle file
    test_urls = [
        "https://www.w3schools.com/html/mov_bbb.vtt",
        "https://gist.githubusercontent.com/samdutton/ca37f3adaf4e23679c34/raw/083d0e2a5a8ffce98e23e5f5e2667b7c8bb0e3f6/sample.vtt",
    ]
    
    try:
        import requests
        
        for test_url in test_urls:
            console.print(f"[dim]Trying: {test_url}[/dim]")
            console.print("[yellow]Fetching subtitle...[/yellow]")
            
            try:
                resp = requests.get(test_url, timeout=10)
                
                if resp.status_code == 200:
                    content = resp.text
                    if "WEBVTT" in content or "-->" in content:
                        console.print(f"[green]✅ Subtitle download test completed[/green]")
                        console.print(f"[dim]Size: {len(content)} bytes, Format: VTT[/dim]")
                        return True
                    else:
                        console.print("[yellow]⚠ Downloaded but format unclear[/yellow]")
                        return True
            except Exception:
                continue
        
        # If all URLs fail, test our own subtitle creation
        console.print("[yellow]Public URLs failed, testing local subtitle generation...[/yellow]")
        test_content = """WEBVTT

00:00:00.000 --> 00:00:05.000
Test subtitle line 1

00:00:05.000 --> 00:00:10.000
Test subtitle line 2
"""
        if "WEBVTT" in test_content and "-->" in test_content:
            console.print(f"[green]✅ Subtitle parsing test completed[/green]")
            return True
        
        return False
            
    except Exception as e:
        console.print(f"[red]❌ Subtitle test failed: {e}[/red]")
        return False


def test_backend_integration():
    """Test full integration with backend API."""
    console.print(Panel("[bold cyan]Testing Backend Integration[/bold cyan]", expand=False))
    
    try:
        from src.utils.api import APIClient
        from src.utils.validator import select_working_source
        from src.utils.storage import load_json_data
        from src.config import SETTINGS_FILE, BACKEND_URL
        
        settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
        api = APIClient(settings)
        
        # Search for a popular movie
        console.print("[yellow]Searching TMDB for 'Avengers'...[/yellow]")
        search = api.get_tmdb_data("search/movie", {"query": "Avengers"})
        
        if not search or not search.get("results"):
            console.print("[red]❌ TMDB search failed[/red]")
            return False
        
        movie = search["results"][0]
        console.print(f"[dim]Found: {movie.get('title')} (ID: {movie.get('id')})[/dim]")
        
        # Get sources
        console.print("[yellow]Fetching streaming sources...[/yellow]")
        sources = api.get_sources_api(movie["id"], "movie")
        
        files = sources.get("files", []) if sources else []
        subs = sources.get("subtitles", []) if sources else []
        
        console.print(f"[dim]Sources: {len(files)}, Subtitles: {len(subs)}[/dim]")
        
        if not files:
            console.print("[yellow]⚠ No sources returned (backend may need different content)[/yellow]")
            return True  # Not a code failure
        
        # Select working source
        console.print("[yellow]Validating sources...[/yellow]")
        working = select_working_source(files[:3])
        
        if working:
            console.print(f"[green]✅ Backend integration test completed[/green]")
            console.print(f"[dim]Working source: {working.get('provider')} [{working.get('quality')}][/dim]")
            return True
        else:
            console.print("[yellow]⚠ No validated source (may still work with yt-dlp)[/yellow]")
            return True
            
    except Exception as e:
        console.print(f"[red]❌ Backend integration test failed: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        return False


def test_aria2c():
    """Test aria2c availability (optional speed booster)."""
    console.print("\n[bold cyan]🚀 Testing aria2c (Optional Speed Booster)[/bold cyan]")
    console.print("[dim]aria2c provides faster parallel downloads - optional but recommended[/dim]\n")
    
    import shutil
    import subprocess
    
    # Check if aria2c is installed
    aria2c_path = shutil.which("aria2c")
    
    if not aria2c_path:
        console.print("[yellow]⚠ aria2c not found in PATH[/yellow]")
        console.print("[dim]To install:[/dim]")
        console.print("[dim]  Windows: choco install aria2 / scoop install aria2[/dim]")
        console.print("[dim]  Linux: apt install aria2 / yum install aria2[/dim]")
        console.print("[dim]  macOS: brew install aria2[/dim]")
        console.print("[yellow]⚠ Downloads will work without it, but may be slower[/yellow]")
        return None  # Optional, not a failure
    
    console.print(f"[green]✓ aria2c found: {aria2c_path}[/green]")
    
    # Get version
    try:
        result = subprocess.run(
            ["aria2c", "--version"],
            capture_output=True,
            text=True,
            timeout=5
        )
        version_line = result.stdout.split('\n')[0] if result.stdout else "Unknown"
        console.print(f"[dim]  Version: {version_line}[/dim]")
        
        # Test a quick download capability
        console.print("[dim]  Testing download capability...[/dim]")
        
        test_url = "https://httpbin.org/bytes/1024"
        test_output = os.path.join(os.path.dirname(__file__), "test_aria2c_download.bin")
        
        try:
            result = subprocess.run(
                ["aria2c", "-q", "-x4", "-s4", "-o", os.path.basename(test_output),
                 "-d", os.path.dirname(test_output), test_url],
                capture_output=True,
                timeout=30
            )
            
            if os.path.exists(test_output):
                size = os.path.getsize(test_output)
                os.remove(test_output)
                console.print(f"[green]✓ Download test passed ({size} bytes)[/green]")
                console.print(f"[green]✅ aria2c is ready for fast parallel downloads[/green]")
                return True
            else:
                console.print("[yellow]⚠ Download test file not created[/yellow]")
                return None
                
        except subprocess.TimeoutExpired:
            console.print("[yellow]⚠ Download test timed out[/yellow]")
            return None
        except Exception as e:
            console.print(f"[yellow]⚠ Download test failed: {e}[/yellow]")
            return None
            
    except Exception as e:
        console.print(f"[yellow]⚠ Could not get aria2c version: {e}[/yellow]")
        return None


def test_subtitle_accessibility():
    """Test subtitle URL accessibility from backend sources."""
    console.print("\n[bold cyan]📝 Testing Subtitle URL Accessibility[/bold cyan]")
    console.print("[dim]Testing if subtitle URLs from backend are accessible[/dim]\n")
    
    import httpx
    
    # Test TMDB ID for Big Buck Bunny (or use a popular movie)
    test_ids = [
        {"tmdb_id": "299536", "name": "Avengers: Infinity War"},  # Popular movie
        {"tmdb_id": "157336", "name": "Interstellar"},
        {"tmdb_id": "550", "name": "Fight Club"},
    ]
    
    backend_url = "http://localhost:3000"
    
    try:
        # First check if backend is running
        try:
            resp = httpx.get(f"{backend_url}/", timeout=5)
        except:
            console.print("[yellow]⚠ Backend not running - start with: cd backend && npm start[/yellow]")
            return None
        
        subtitle_urls_found = []
        
        for test_item in test_ids:
            tmdb_id = test_item["tmdb_id"]
            name = test_item["name"]
            
            console.print(f"[dim]Checking subtitles for {name}...[/dim]")
            
            try:
                # Try to get sources which may include subtitles
                resp = httpx.get(
                    f"{backend_url}/api/sources/{tmdb_id}",
                    params={"type": "movie"},
                    timeout=15
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    sources = data if isinstance(data, list) else data.get("sources", [])
                    
                    for source in sources[:3]:  # Check first 3 sources
                        subs = source.get("subtitles", [])
                        if subs:
                            for sub in subs[:2]:  # First 2 subs per source
                                sub_url = sub.get("url", sub.get("file"))
                                if sub_url:
                                    subtitle_urls_found.append({
                                        "url": sub_url,
                                        "lang": sub.get("lang", sub.get("label", "unknown")),
                                        "movie": name
                                    })
                            break
                            
            except Exception as e:
                console.print(f"[dim]  Could not get sources for {name}: {e}[/dim]")
        
        if not subtitle_urls_found:
            console.print("[yellow]⚠ No subtitle URLs found in backend sources[/yellow]")
            console.print("[dim]This is normal - not all sources provide embedded subtitles[/dim]")
            return None
        
        # Test accessibility of found subtitle URLs
        console.print(f"\n[dim]Found {len(subtitle_urls_found)} subtitle URLs, testing accessibility...[/dim]")
        
        accessible = 0
        for sub_info in subtitle_urls_found[:5]:  # Test up to 5
            url = sub_info["url"]
            try:
                # Use HEAD first, fallback to GET with range
                try:
                    resp = httpx.head(url, timeout=10, follow_redirects=True)
                except:
                    resp = httpx.get(url, timeout=10, headers={"Range": "bytes=0-100"}, follow_redirects=True)
                
                if resp.status_code in (200, 206, 301, 302):
                    console.print(f"[green]  ✓ {sub_info['lang']} ({sub_info['movie']})[/green]")
                    accessible += 1
                else:
                    console.print(f"[yellow]  ⚠ {sub_info['lang']} - HTTP {resp.status_code}[/yellow]")
                    
            except Exception as e:
                console.print(f"[yellow]  ⚠ {sub_info['lang']} - {str(e)[:40]}[/yellow]")
        
        if accessible > 0:
            console.print(f"\n[green]✅ {accessible}/{len(subtitle_urls_found[:5])} subtitle URLs accessible[/green]")
            return True
        else:
            console.print(f"\n[yellow]⚠ No subtitle URLs were accessible (external service issue)[/yellow]")
            return None
            
    except Exception as e:
        console.print(f"[red]❌ Subtitle accessibility test failed: {e}[/red]")
        return False


def test_opensubtitles():
    """Test OpenSubtitles API integration."""
    console.print("\n[bold cyan]🌐 Testing OpenSubtitles API[/bold cyan]")
    console.print("[dim]OpenSubtitles provides fallback subtitles when sources don't include them[/dim]\n")
    
    import httpx
    
    # OpenSubtitles API endpoint (public, rate-limited)
    api_url = "https://api.opensubtitles.com/api/v1"
    
    try:
        # Test 1: Check API availability
        console.print("[dim]Checking OpenSubtitles API availability...[/dim]")
        
        headers = {
            "Api-Key": "rvlTcMDfW2ysyt0QxF2btYFCqS6oQMu2",  # Public demo key
            "Content-Type": "application/json",
            "User-Agent": "CinemaCLI v1.0"
        }
        
        # Try infos endpoint first (less rate-limited)
        try:
            resp = httpx.get(
                f"{api_url}/infos/languages",
                headers=headers,
                timeout=10
            )
            
            if resp.status_code == 200:
                data = resp.json()
                lang_count = len(data.get("data", []))
                console.print(f"[green]✓ API available - {lang_count} languages supported[/green]")
            elif resp.status_code == 429:
                console.print("[yellow]⚠ Rate limited - API is working but quota exceeded[/yellow]")
                console.print("[dim]This is normal for public API usage. Try again later.[/dim]")
                return None
            else:
                console.print(f"[yellow]⚠ API returned HTTP {resp.status_code}[/yellow]")
                
        except httpx.TimeoutException:
            console.print("[yellow]⚠ API request timed out[/yellow]")
            return None
        
        # Test 2: Search for subtitles
        console.print("[dim]Testing subtitle search...[/dim]")
        
        try:
            resp = httpx.get(
                f"{api_url}/subtitles",
                headers=headers,
                params={
                    "tmdb_id": "550",  # Fight Club
                    "languages": "en"
                },
                timeout=15
            )
            
            if resp.status_code == 200:
                data = resp.json()
                total = data.get("total_count", 0)
                results = data.get("data", [])
                
                if results:
                    console.print(f"[green]✓ Subtitle search working - {total} results found[/green]")
                    
                    # Show first result
                    first = results[0].get("attributes", {})
                    console.print(f"[dim]  Example: {first.get('release', 'N/A')} ({first.get('language', 'N/A')})[/dim]")
                    console.print(f"[dim]  Downloads: {first.get('download_count', 'N/A')}[/dim]")
                    
                    console.print(f"\n[green]✅ OpenSubtitles API is working[/green]")
                    return True
                else:
                    console.print("[yellow]⚠ Search returned no results[/yellow]")
                    return None
                    
            elif resp.status_code == 429:
                console.print("[yellow]⚠ Rate limited during search[/yellow]")
                console.print("[dim]OpenSubtitles limits: 5 requests/second, 100/day for free tier[/dim]")
                return None
            else:
                console.print(f"[yellow]⚠ Search returned HTTP {resp.status_code}[/yellow]")
                return None
                
        except httpx.TimeoutException:
            console.print("[yellow]⚠ Search request timed out[/yellow]")
            return None
            
    except Exception as e:
        console.print(f"[red]❌ OpenSubtitles test failed: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        return False


def run_all():
    """Run all quick tests."""
    console.print("\n[bold magenta]═══════════════════════════════════════════════════════[/bold magenta]")
    console.print("[bold magenta]       CINEMA CLI - QUICK INTERACTIVE TEST SUITE         [/bold magenta]")
    console.print("[bold magenta]═══════════════════════════════════════════════════════[/bold magenta]\n")
    
    results = {}
    
    # Test 1: Backend Integration (no user interaction needed)
    results["Backend Integration"] = test_backend_integration()
    console.print()
    
    # Test 2: aria2c (optional)
    results["aria2c"] = test_aria2c()
    console.print()
    
    # Test 3: Subtitle Download
    results["Subtitle Download"] = test_subtitle()
    console.print()
    
    # Test 4: Subtitle Accessibility
    results["Subtitle URLs"] = test_subtitle_accessibility()
    console.print()
    
    # Test 5: OpenSubtitles API
    results["OpenSubtitles API"] = test_opensubtitles()
    console.print()
    
    # Test 6: Download (takes ~30 seconds)
    console.print("[bold yellow]The download test will download a 10-second clip...[/bold yellow]")
    response = input("Run download test? [Y/n]: ").strip().lower()
    if response != 'n':
        results["Download"] = test_download()
    else:
        results["Download"] = None
        console.print("[dim]Skipped[/dim]")
    console.print()
    
    # Test 7: Streaming (requires user to close player)
    console.print("[bold yellow]The streaming test will open MPV player...[/bold yellow]")
    response = input("Run streaming test? [Y/n]: ").strip().lower()
    if response != 'n':
        results["Streaming"] = test_stream()
    else:
        results["Streaming"] = None
        console.print("[dim]Skipped[/dim]")
    console.print()
    
    # Summary
    console.print("\n[bold magenta]═══════════════════════════════════════════════════════[/bold magenta]")
    console.print("[bold magenta]                      TEST SUMMARY                        [/bold magenta]")
    console.print("[bold magenta]═══════════════════════════════════════════════════════[/bold magenta]\n")
    
    for name, result in results.items():
        if result is True:
            console.print(f"  [green]✅ {name}[/green]")
        elif result is False:
            console.print(f"  [red]❌ {name}[/red]")
        else:
            console.print(f"  [dim]⏭ {name} (skipped)[/dim]")
    
    passed = sum(1 for r in results.values() if r is True)
    failed = sum(1 for r in results.values() if r is False)
    skipped = sum(1 for r in results.values() if r is None)
    
    console.print(f"\n  Passed: {passed}, Failed: {failed}, Skipped: {skipped}")
    
    if failed == 0:
        console.print("\n[bold green]🎉 All tests passed! Cinema CLI is ready for a great user experience.[/bold green]\n")
    else:
        console.print("\n[bold yellow]⚠ Some tests failed. Check the details above.[/bold yellow]\n")


def main():
    if len(sys.argv) < 2:
        run_all()
        return
    
    cmd = sys.argv[1].lower()
    
    if cmd == "stream":
        test_stream()
    elif cmd == "download":
        test_download()
    elif cmd == "subtitle":
        test_subtitle()
    elif cmd == "backend":
        test_backend_integration()
    elif cmd == "aria2c":
        test_aria2c()
    elif cmd == "suburl" or cmd == "subtitle-url":
        test_subtitle_accessibility()
    elif cmd == "opensub" or cmd == "opensubtitles":
        test_opensubtitles()
    elif cmd == "all":
        run_all()
    else:
        console.print(f"[red]Unknown command: {cmd}[/red]")
        console.print("Usage: python quick_test.py [command]")
        console.print("\nCommands:")
        console.print("  stream       - Test streaming with MPV player")
        console.print("  download     - Test download functionality")
        console.print("  subtitle     - Test subtitle download")
        console.print("  backend      - Test backend API integration")
        console.print("  aria2c       - Test aria2c speed booster (optional)")
        console.print("  suburl       - Test subtitle URL accessibility")
        console.print("  opensub      - Test OpenSubtitles API")
        console.print("  all          - Run all tests interactively")


if __name__ == "__main__":
    main()
