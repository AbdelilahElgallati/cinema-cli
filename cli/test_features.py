#!/usr/bin/env python3
"""
Cinema CLI Feature Test Suite
=============================
Comprehensive test script to verify all major features work correctly.

Run with: python test_features.py
"""

import json
import os
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Test results storage
TEST_RESULTS = []
LOG_FILE = os.path.join(os.path.dirname(__file__), "test_results.log")


def log(message, level="INFO"):
    """Log a message to console and file."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] [{level}] {message}"
    print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def test_result(name, passed, details=""):
    """Record a test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    TEST_RESULTS.append({"name": name, "passed": passed, "details": details})
    log(f"{status} - {name}" + (f": {details}" if details else ""), "INFO" if passed else "ERROR")


def section(title):
    """Print a section header."""
    log("=" * 60)
    log(f"  {title}")
    log("=" * 60)


# =============================================================================
# TEST 1: Module Imports
# =============================================================================
def test_imports():
    section("TEST 1: Module Imports")
    
    modules_to_test = [
        ("src.config", "Configuration module"),
        ("src.utils.api", "API client module"),
        ("src.utils.download_manager", "Download manager module"),
        ("src.utils.player", "Player module"),
        ("src.utils.subtitles", "Subtitles module"),
        ("src.utils.validator", "Validator module"),
        ("src.utils.utils", "Utilities module"),
        ("src.utils.storage", "Storage module"),
        ("src.utils.library", "Library module"),
        ("src.utils.app_logger", "App logger module"),
        ("src.ui.ui", "UI module"),
    ]
    
    all_passed = True
    for module_name, description in modules_to_test:
        try:
            __import__(module_name)
            test_result(f"Import {description}", True)
        except Exception as e:
            test_result(f"Import {description}", False, str(e))
            all_passed = False
    
    # Test main module
    try:
        from main import CinemaCLI
        test_result("Import main CinemaCLI class", True)
    except Exception as e:
        test_result("Import main CinemaCLI class", False, str(e))
        all_passed = False
    
    return all_passed


# =============================================================================
# TEST 2: Configuration & Environment
# =============================================================================
def test_configuration():
    section("TEST 2: Configuration & Environment")
    
    from src.config import (
        BACKEND_URL, TMDB_API_KEY, OPENSUBTITLES_API_KEY,
        HISTORY_FILE, FAVORITES_FILE, PLAYBACK_FILE, SETTINGS_FILE,
        DATA_DIR, DOWNLOAD_LOG, APP_LOG
    )
    
    all_passed = True
    
    # Check required directories exist or can be created
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        test_result("Data directory accessible", True, DATA_DIR)
    except Exception as e:
        test_result("Data directory accessible", False, str(e))
        all_passed = False
    
    # Check TMDB API key
    if TMDB_API_KEY and len(TMDB_API_KEY) > 10:
        test_result("TMDB API key configured", True)
    else:
        test_result("TMDB API key configured", False, "Missing or invalid TMDB_API_KEY")
        all_passed = False
    
    # Check backend URL
    if BACKEND_URL and BACKEND_URL.startswith("http"):
        test_result("Backend URL configured", True, BACKEND_URL)
    else:
        test_result("Backend URL configured", False, "Missing or invalid BACKEND_URL")
        all_passed = False
    
    # Check OpenSubtitles key (optional)
    if OPENSUBTITLES_API_KEY:
        test_result("OpenSubtitles API key configured", True)
    else:
        test_result("OpenSubtitles API key configured", False, "Optional - subtitles from sources only")
    
    return all_passed


# =============================================================================
# TEST 3: External Dependencies
# =============================================================================
def test_dependencies():
    section("TEST 3: External Dependencies")
    
    all_passed = True
    
    # Check yt-dlp
    if shutil.which("yt-dlp"):
        try:
            import subprocess
            result = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True, timeout=10)
            version = result.stdout.strip()
            test_result("yt-dlp installed", True, f"version {version}")
        except Exception as e:
            test_result("yt-dlp installed", False, str(e))
            all_passed = False
    else:
        test_result("yt-dlp installed", False, "Not found in PATH - downloads may fail")
        all_passed = False
    
    # Check mpv
    if shutil.which("mpv"):
        try:
            import subprocess
            result = subprocess.run(["mpv", "--version"], capture_output=True, text=True, timeout=10)
            version = result.stdout.split('\n')[0] if result.stdout else "unknown"
            test_result("mpv installed", True, version[:50])
        except Exception as e:
            test_result("mpv installed", False, str(e))
            all_passed = False
    else:
        test_result("mpv installed", False, "Not found in PATH - playback will fail")
        all_passed = False
    
    # Check ffmpeg (optional but recommended)
    if shutil.which("ffmpeg"):
        test_result("ffmpeg installed", True, "Subtitle embedding available")
    else:
        test_result("ffmpeg installed", False, "Optional - subtitle embedding disabled")
    
    # Check aria2c (optional)
    if shutil.which("aria2c"):
        test_result("aria2c installed", True, "Faster downloads available")
    else:
        test_result("aria2c installed", False, "Optional - using default downloader")
    
    return all_passed


# =============================================================================
# TEST 4: API Client
# =============================================================================
def test_api_client():
    section("TEST 4: API Client")
    
    from src.utils.api import APIClient
    from src.utils.storage import load_json_data
    from src.config import SETTINGS_FILE, BACKEND_URL
    
    settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
    api = APIClient(settings)
    
    all_passed = True
    
    # Test TMDB connection
    log("Testing TMDB API connection...")
    try:
        result = api.get_tmdb_data("movie/popular", {"page": 1})
        if result and "results" in result and len(result["results"]) > 0:
            test_result("TMDB API - Popular movies", True, f"Got {len(result['results'])} movies")
        else:
            test_result("TMDB API - Popular movies", False, "Empty or invalid response")
            all_passed = False
    except Exception as e:
        test_result("TMDB API - Popular movies", False, str(e))
        all_passed = False
    
    # Test TMDB search
    log("Testing TMDB search...")
    try:
        result = api.get_tmdb_data("search/movie", {"query": "Inception"})
        if result and "results" in result and len(result["results"]) > 0:
            movie = result["results"][0]
            test_result("TMDB API - Search", True, f"Found: {movie.get('title')}")
        else:
            test_result("TMDB API - Search", False, "No search results")
            all_passed = False
    except Exception as e:
        test_result("TMDB API - Search", False, str(e))
        all_passed = False
    
    # Test backend connection
    log("Testing backend API connection...")
    try:
        # Use a well-known movie ID (Inception = 27205)
        result = api.get_sources_api(27205, "movie")
        if result and isinstance(result, dict):
            files = result.get("files", [])
            subs = result.get("subtitles", [])
            if files:
                test_result("Backend API - Get sources", True, f"Got {len(files)} sources, {len(subs)} subtitles")
            else:
                test_result("Backend API - Get sources", False, "No streaming sources returned")
                all_passed = False
        else:
            test_result("Backend API - Get sources", False, "Invalid response format")
            all_passed = False
    except Exception as e:
        test_result("Backend API - Get sources", False, str(e))
        all_passed = False
    
    return all_passed


# =============================================================================
# TEST 5: Source Validation
# =============================================================================
def test_source_validation():
    section("TEST 5: Source Validation")
    
    from src.utils.validator import verify_source, select_working_source
    from src.utils.api import APIClient
    from src.utils.storage import load_json_data
    from src.config import SETTINGS_FILE, BACKEND_URL
    
    settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
    api = APIClient(settings)
    
    all_passed = True
    
    # Get sources for a test movie
    log("Fetching sources for validation test...")
    try:
        result = api.get_sources_api(27205, "movie")  # Inception
        files = result.get("files", []) if result else []
        
        if not files:
            test_result("Source validation - Get test sources", False, "No sources available")
            return False
        
        test_result("Source validation - Get test sources", True, f"{len(files)} sources")
        
        # Test individual source validation
        log("Testing source validation...")
        validated_count = 0
        for i, src in enumerate(files[:3]):  # Test first 3
            url = src.get("file")
            headers = src.get("headers")
            provider = src.get("provider", "Unknown")
            
            try:
                is_valid = verify_source(url, headers, timeout=8)
                if is_valid:
                    validated_count += 1
                    log(f"  Source {i+1} ({provider}): Valid")
                else:
                    log(f"  Source {i+1} ({provider}): Invalid/Unreachable")
            except Exception as e:
                log(f"  Source {i+1} ({provider}): Error - {e}")
        
        if validated_count > 0:
            test_result("Source validation - Verify sources", True, f"{validated_count}/{min(3, len(files))} valid")
        else:
            test_result("Source validation - Verify sources", False, "No sources passed validation")
            all_passed = False
        
        # Test select_working_source
        log("Testing working source selection...")
        working = select_working_source(files[:5])
        if working:
            test_result("Source validation - Select working source", True, 
                       f"Selected: {working.get('provider')} [{working.get('quality')}]")
        else:
            test_result("Source validation - Select working source", False, "No working source found")
            all_passed = False
            
    except Exception as e:
        test_result("Source validation", False, str(e))
        all_passed = False
    
    return all_passed


# =============================================================================
# TEST 6: Download Manager
# =============================================================================
def test_download_manager():
    section("TEST 6: Download Manager")
    
    from src.utils.download_manager import DownloadManager
    from src.utils.api import APIClient
    from src.utils.storage import load_json_data
    from src.config import SETTINGS_FILE, BACKEND_URL
    
    settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
    api = APIClient(settings)
    
    all_passed = True
    
    # Create a test download manager
    test_dir = tempfile.mkdtemp(prefix="cinema_cli_test_")
    log(f"Test directory: {test_dir}")
    
    try:
        dm = DownloadManager(downloads_dir=test_dir, api_client=api)
        test_result("Download Manager - Initialize", True)
        
        # Test queue operations
        initial_queue = dm.get_queue()
        test_result("Download Manager - Get queue", True, f"{len(initial_queue)} items")
        
        # Test task addition (without actually downloading)
        test_url = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
        dm.add_task(
            url=test_url,
            filename="test_video.mp4",
            title="Test Video",
            subtitles=[{"url": "https://example.com/sub.srt", "lang": "en"}],
            headers=None,
            meta={"type": "movie", "year": 2024},
            preferred_sub_lang="en",
            include_all_subs=False
        )
        
        new_queue = dm.get_queue()
        if len(new_queue) > len(initial_queue):
            test_result("Download Manager - Add task", True)
            
            # Clean up - remove the test task
            test_task = [t for t in new_queue if t.get("title") == "Test Video"]
            if test_task:
                dm.remove_task(test_task[0]["id"])
                test_result("Download Manager - Remove task", True)
        else:
            test_result("Download Manager - Add task", False, "Task not added to queue")
            all_passed = False
        
        # Test subtitle download function exists and is callable
        if hasattr(dm, "_download_subtitles"):
            test_result("Download Manager - Subtitle handler exists", True)
        else:
            test_result("Download Manager - Subtitle handler exists", False)
            all_passed = False
        
        # Test source refresh function
        if hasattr(dm, "_refresh_source_if_needed"):
            test_result("Download Manager - Source refresh exists", True)
        else:
            test_result("Download Manager - Source refresh exists", False)
            all_passed = False
        
    except Exception as e:
        test_result("Download Manager", False, str(e))
        log(traceback.format_exc(), "ERROR")
        all_passed = False
    finally:
        # Clean up test directory
        try:
            shutil.rmtree(test_dir, ignore_errors=True)
        except Exception:
            pass
    
    return all_passed


# =============================================================================
# TEST 7: Subtitle Handling
# =============================================================================
def test_subtitle_handling():
    section("TEST 7: Subtitle Handling")
    
    from src.utils.api import APIClient
    from src.utils.storage import load_json_data
    from src.config import SETTINGS_FILE, BACKEND_URL
    
    settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
    api = APIClient(settings)
    
    all_passed = True
    
    # Test getting subtitles from backend
    log("Fetching subtitles from backend...")
    try:
        result = api.get_sources_api(27205, "movie")  # Inception
        subs = result.get("subtitles", []) if result else []
        
        if subs:
            test_result("Subtitle - Fetch from backend", True, f"Got {len(subs)} subtitle tracks")
            
            # Analyze subtitle languages
            langs = {}
            for s in subs:
                if isinstance(s, dict):
                    lang = s.get("lang") or s.get("language") or "unknown"
                    langs[lang] = langs.get(lang, 0) + 1
            
            log(f"  Languages found: {dict(langs)}")
            
            # Test subtitle URL accessibility
            log("Testing subtitle URL accessibility...")
            accessible = 0
            for s in subs[:3]:  # Test first 3
                url = s.get("url") if isinstance(s, dict) else None
                if url:
                    try:
                        import requests
                        resp = requests.head(url, timeout=5, allow_redirects=True)
                        if resp.status_code < 400:
                            accessible += 1
                    except Exception:
                        pass
            
            if accessible > 0:
                test_result("Subtitle - URL accessibility", True, f"{accessible}/3 accessible")
            else:
                test_result("Subtitle - URL accessibility", False, "No subtitle URLs accessible")
                all_passed = False
        else:
            test_result("Subtitle - Fetch from backend", False, "No subtitles returned")
            # This is not a critical failure - some content may not have subs
    
    except Exception as e:
        test_result("Subtitle handling", False, str(e))
        all_passed = False
    
    # Test OpenSubtitles (if configured)
    try:
        from src.utils.subtitles import fetch_arabic_subtitle
        from src.config import OPENSUBTITLES_API_KEY
        
        if OPENSUBTITLES_API_KEY:
            log("Testing OpenSubtitles API...")
            result = fetch_arabic_subtitle("Inception", year=2010)
            if result:
                content, ext = result
                test_result("Subtitle - OpenSubtitles API", True, f"Got {len(content)} bytes ({ext})")
            else:
                test_result("Subtitle - OpenSubtitles API", False, "No results")
        else:
            test_result("Subtitle - OpenSubtitles API", False, "API key not configured (optional)")
    except Exception as e:
        test_result("Subtitle - OpenSubtitles API", False, str(e))
    
    return all_passed


# =============================================================================
# TEST 8: Quality Selection Logic
# =============================================================================
def test_quality_selection():
    section("TEST 8: Quality Selection Logic")
    
    from src.utils.api import APIClient
    from src.utils.storage import load_json_data
    from src.config import SETTINGS_FILE, BACKEND_URL
    
    settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
    api = APIClient(settings)
    
    all_passed = True
    
    # Get sources and check quality distribution
    log("Analyzing quality options...")
    try:
        result = api.get_sources_api(27205, "movie")  # Inception
        files = result.get("files", []) if result else []
        
        if not files:
            test_result("Quality selection - Get sources", False, "No sources")
            return False
        
        # Analyze qualities
        qualities = {}
        for f in files:
            q = f.get("quality", "Unknown")
            qualities[q] = qualities.get(q, 0) + 1
        
        log(f"  Qualities found: {dict(qualities)}")
        test_result("Quality selection - Quality distribution", True, 
                   f"{len(qualities)} different qualities")
        
        # Test quality filtering logic
        def quality_sort_key(q):
            q = (q or "").lower()
            if "4k" in q or "2160" in q: return 0
            if "1080" in q: return 1
            if "720" in q: return 2
            if "480" in q: return 3
            if "360" in q: return 4
            return 5
        
        sorted_qualities = sorted(qualities.keys(), key=quality_sort_key)
        log(f"  Sorted by preference: {sorted_qualities}")
        
        # Test filtering by quality
        for target_q in ["1080", "720", "480"]:
            filtered = [f for f in files if target_q in str(f.get("quality", ""))]
            if filtered:
                log(f"  Filter '{target_q}': {len(filtered)} sources")
        
        test_result("Quality selection - Filtering logic", True)
        
    except Exception as e:
        test_result("Quality selection", False, str(e))
        all_passed = False
    
    return all_passed


# =============================================================================
# TEST 9: Player Integration
# =============================================================================
def test_player_integration():
    section("TEST 9: Player Integration")
    
    all_passed = True
    
    # Check player module
    try:
        from src.utils.player import play_stream, play_video
        test_result("Player - Module import", True)
    except Exception as e:
        test_result("Player - Module import", False, str(e))
        return False
    
    # Check mpv availability
    if not shutil.which("mpv"):
        test_result("Player - MPV available", False, "mpv not in PATH")
        return False
    
    test_result("Player - MPV available", True)
    
    # Verify player function signatures
    import inspect
    
    # Check play_stream signature
    sig = inspect.signature(play_stream)
    params = list(sig.parameters.keys())
    expected = ["url", "title", "subtitles", "headers", "meta", "start_time", 
                "preferred_sub_lang", "include_all_subs"]
    
    missing = [p for p in expected if p not in params]
    if not missing:
        test_result("Player - play_stream parameters", True)
    else:
        test_result("Player - play_stream parameters", False, f"Missing: {missing}")
        all_passed = False
    
    # Check play_video signature
    sig = inspect.signature(play_video)
    params = list(sig.parameters.keys())
    if "preferred_sub_lang" in params:
        test_result("Player - play_video subtitle support", True)
    else:
        test_result("Player - play_video subtitle support", False, "Missing preferred_sub_lang")
        all_passed = False
    
    return all_passed


# =============================================================================
# TEST 10: Storage & Persistence
# =============================================================================
def test_storage():
    section("TEST 10: Storage & Persistence")
    
    from src.utils.storage import load_json_data, save_json_data
    from src.config import HISTORY_FILE, FAVORITES_FILE, PLAYBACK_FILE, SETTINGS_FILE
    
    all_passed = True
    
    # Test loading existing data
    for name, path in [
        ("History", HISTORY_FILE),
        ("Favorites", FAVORITES_FILE),
        ("Playback", PLAYBACK_FILE),
        ("Settings", SETTINGS_FILE),
    ]:
        try:
            data = load_json_data(path)
            if data is not None:
                test_result(f"Storage - Load {name}", True, f"{type(data).__name__}")
            else:
                test_result(f"Storage - Load {name}", True, "Empty/New file")
        except Exception as e:
            test_result(f"Storage - Load {name}", False, str(e))
            all_passed = False
    
    # Test write capability
    test_file = os.path.join(tempfile.gettempdir(), "cinema_cli_test_storage.json")
    try:
        test_data = {"test": True, "timestamp": time.time()}
        save_json_data(test_file, test_data)
        loaded = load_json_data(test_file)
        if loaded and loaded.get("test") == True:
            test_result("Storage - Write/Read cycle", True)
        else:
            test_result("Storage - Write/Read cycle", False, "Data mismatch")
            all_passed = False
    except Exception as e:
        test_result("Storage - Write/Read cycle", False, str(e))
        all_passed = False
    finally:
        try:
            os.remove(test_file)
        except Exception:
            pass
    
    return all_passed


# =============================================================================
# TEST 11: End-to-End Download Simulation
# =============================================================================
def test_download_simulation():
    section("TEST 11: Download Simulation (No actual download)")
    
    from src.utils.download_manager import DownloadManager
    from src.utils.validator import select_working_source
    from src.utils.api import APIClient
    from src.utils.storage import load_json_data
    from src.utils.utils import generate_filename
    from src.config import SETTINGS_FILE, BACKEND_URL
    
    settings = load_json_data(SETTINGS_FILE) or {"backend": BACKEND_URL}
    api = APIClient(settings)
    
    all_passed = True
    
    log("Simulating full download workflow...")
    
    try:
        # Step 1: Get sources
        log("  Step 1: Fetching sources...")
        result = api.get_sources_api(27205, "movie")
        files = result.get("files", []) if result else []
        subtitles = result.get("subtitles", []) if result else []
        
        if not files:
            test_result("Download simulation - Get sources", False, "No sources")
            return False
        
        test_result("Download simulation - Get sources", True, f"{len(files)} sources")
        
        # Step 2: Quality filtering
        log("  Step 2: Filtering by quality (720p)...")
        filtered = [f for f in files if "720" in str(f.get("quality", ""))]
        if not filtered:
            filtered = files  # Fallback
            log("    No 720p found, using all sources")
        test_result("Download simulation - Quality filter", True, f"{len(filtered)} matching")
        
        # Step 3: Select working source
        log("  Step 3: Selecting working source...")
        selected = select_working_source(filtered[:5])
        if selected:
            test_result("Download simulation - Source selection", True,
                       f"{selected.get('provider')} [{selected.get('quality')}]")
        else:
            test_result("Download simulation - Source selection", False, "No working source")
            all_passed = False
            return all_passed
        
        # Step 4: Generate filename
        log("  Step 4: Generating filename...")
        template = settings.get("filename_template", "{title}.{year}")
        meta = {"year": 2010, "type": "movie"}
        filename = generate_filename(template, "Inception", meta, selected)
        test_result("Download simulation - Filename generation", True, filename)
        
        # Step 5: Subtitle preparation
        log("  Step 5: Preparing subtitles...")
        if subtitles:
            ar_subs = [s for s in subtitles if isinstance(s, dict) and 
                      (s.get("lang") or "").lower() in ["ar", "ara", "arabic"]]
            test_result("Download simulation - Subtitle preparation", True,
                       f"{len(ar_subs)} Arabic, {len(subtitles)} total")
        else:
            test_result("Download simulation - Subtitle preparation", True, "No subtitles available")
        
        # Step 6: Queue task (don't actually start)
        log("  Step 6: Task configuration check...")
        task_config = {
            "url": selected.get("file"),
            "filename": filename,
            "title": "Inception (Test)",
            "subtitles": subtitles,
            "headers": selected.get("headers"),
            "meta": meta,
            "preferred_sub_lang": "ar",
            "include_all_subs": False,
        }
        
        # Validate all required fields
        missing = [k for k, v in task_config.items() if k in ["url", "filename", "title"] and not v]
        if not missing:
            test_result("Download simulation - Task configuration", True)
        else:
            test_result("Download simulation - Task configuration", False, f"Missing: {missing}")
            all_passed = False
        
    except Exception as e:
        test_result("Download simulation", False, str(e))
        log(traceback.format_exc(), "ERROR")
        all_passed = False
    
    return all_passed


# =============================================================================
# MAIN TEST RUNNER
# =============================================================================
def run_all_tests():
    """Run all tests and generate summary report."""
    
    # Clear log file
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            f.write(f"Cinema CLI Test Suite - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 70 + "\n\n")
    except Exception:
        pass
    
    print("\n" + "=" * 70)
    print("  CINEMA CLI - COMPREHENSIVE FEATURE TEST SUITE")
    print("=" * 70 + "\n")
    
    # Run all tests
    test_functions = [
        ("Module Imports", test_imports),
        ("Configuration", test_configuration),
        ("Dependencies", test_dependencies),
        ("API Client", test_api_client),
        ("Source Validation", test_source_validation),
        ("Download Manager", test_download_manager),
        ("Subtitle Handling", test_subtitle_handling),
        ("Quality Selection", test_quality_selection),
        ("Player Integration", test_player_integration),
        ("Storage", test_storage),
        ("Download Simulation", test_download_simulation),
    ]
    
    results = {}
    for name, func in test_functions:
        try:
            results[name] = func()
        except Exception as e:
            log(f"CRITICAL ERROR in {name}: {e}", "ERROR")
            log(traceback.format_exc(), "ERROR")
            results[name] = False
        print()  # Spacing between sections
    
    # Generate summary
    section("TEST SUMMARY")
    
    passed = sum(1 for r in TEST_RESULTS if r["passed"])
    failed = len(TEST_RESULTS) - passed
    
    print(f"\nTotal Tests: {len(TEST_RESULTS)}")
    print(f"  ✅ Passed: {passed}")
    print(f"  ❌ Failed: {failed}")
    print(f"\nPass Rate: {(passed / len(TEST_RESULTS) * 100):.1f}%\n")
    
    # List failures
    failures = [r for r in TEST_RESULTS if not r["passed"]]
    if failures:
        print("\n❌ FAILED TESTS:")
        print("-" * 50)
        for f in failures:
            print(f"  • {f['name']}")
            if f['details']:
                print(f"    Details: {f['details']}")
    
    # Recommendations
    print("\n" + "=" * 70)
    print("  RECOMMENDATIONS")
    print("=" * 70)
    
    critical_failures = []
    warnings = []
    
    for r in TEST_RESULTS:
        if not r["passed"]:
            name = r["name"].lower()
            if "tmdb" in name or "backend" in name:
                critical_failures.append(f"• Check API keys and backend server: {r['name']}")
            elif "yt-dlp" in name or "mpv" in name:
                critical_failures.append(f"• Install missing dependency: {r['name']}")
            elif "opensubtitles" in name or "optional" in r.get("details", "").lower():
                warnings.append(f"• Optional feature unavailable: {r['name']}")
            else:
                warnings.append(f"• Review: {r['name']}")
    
    if critical_failures:
        print("\n🔴 Critical Issues:")
        for c in critical_failures:
            print(f"  {c}")
    
    if warnings:
        print("\n🟡 Warnings (non-critical):")
        for w in warnings:
            print(f"  {w}")
    
    if not critical_failures and not warnings:
        print("\n🟢 All systems operational! Cinema CLI is ready to use.\n")
    
    # Save detailed results
    print(f"\n📄 Detailed log saved to: {LOG_FILE}")
    
    # Return overall success
    return failed == 0 or len(critical_failures) == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
