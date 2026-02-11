import os
import time
import threading
import shutil
from unittest.mock import MagicMock, patch

# Mocking dependencies
import sys
from pathlib import Path

# Add current directory to path
sys.path.append(os.getcwd())

from src.utils.download_manager import DownloadManager

def test_download_manager_responsiveness():
    print("Testing DownloadManager responsiveness...")
    
    # Mock source manager and settings
    mock_source_manager = MagicMock()
    
    with patch('src.utils.download_manager.load_json_data', return_value=[]), \
         patch('src.utils.download_manager.save_json_data'), \
         patch('src.utils.download_manager.SubtitleManager'), \
         patch('src.utils.download_manager.SettingsManager') as mock_settings:
        
        mock_settings.return_value.subtitle_languages = ["en", "ar"]
        mock_settings.return_value.local_library_paths = []
        
        dm = DownloadManager(mock_source_manager)
        
        # Add a mock task
        task_id = "test-task"
        task = {
            "id": task_id,
            "filename": "test_video.mp4",
            "title": "Test Video",
            "status": "pending",
            "progress": 0,
            "eta": "00:00",
            "url": "http://example.com/video.mp4"
        }
        dm.queue = [task]
        
        # Test responsiveness of get_queue while logic is running
        def simulate_worker():
            # Simulate a long running task update
            for i in range(101):
                dm._update_task_safely(task, progress=i)
                time.sleep(0.01)
        
        thread = threading.Thread(target=simulate_worker)
        thread.start()
        
        # Check if we can get the queue without blocking
        start_time = time.time()
        for _ in range(10):
            q = dm.get_queue()
            assert len(q) == 1
            time.sleep(0.05)
        
        end_time = time.time()
        print(f"Queue access time for 10 calls: {end_time - start_time:.4f}s")
        assert (end_time - start_time) < 1.0 # Should be very fast
        
        thread.join()
        print("Responsiveness test passed.")

if __name__ == "__main__":
    os.makedirs("tests", exist_ok=True)
    test_download_manager_responsiveness()
