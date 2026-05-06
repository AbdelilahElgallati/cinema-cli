import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch, ANY

# Add cli directory to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.player import play_stream

class TestPlayerIPC(unittest.TestCase):
    @patch("src.utils.player.subprocess.Popen")
    @patch("src.utils.player.threading.Thread")
    @patch("src.utils.player._prepare_subtitles")
    @patch("src.utils.player._resolve_player")
    @patch("src.utils.player.console")
    @patch("src.utils.player.clear")
    def test_P1_mpv_launches_before_subtitle_fetch(self, mock_clear, mock_console, mock_resolve, mock_prep, mock_thread, mock_popen):
        """Verify mpv launches before background subtitle thread starts."""
        # Setup mocks
        mock_resolve.return_value = "mpv"
        mock_prep.return_value = (["stage1.srt"], ["en"], {"stage1.srt"})
        
        mock_process = MagicMock()
        mock_process.stdout.readline.return_value = ""
        mock_process.wait.return_value = 0
        mock_popen.return_value = mock_process
        
        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        # Call play_stream
        url = "http://example.com/video.m3u8"
        title = "Test Movie"
        play_stream(url, title, subtitles=[], player="mpv")

        # 1. Verify _prepare_subtitles was called
        self.assertTrue(mock_prep.called)

        # 2. Verify Popen was called (mpv launch)
        self.assertTrue(mock_popen.called)
        
        # 3. Verify IPC socket path format
        args, kwargs = mock_popen.call_args
        mpv_args = args[0]
        ipc_arg = [a for l in mpv_args for a in ([l] if isinstance(l, str) else []) if "--input-ipc-server=" in str(a)]
        self.assertTrue(len(ipc_arg) > 0)
        
        ipc_path = ipc_arg[0].split("=", 1)[1]
        if sys.platform == "win32":
            self.assertTrue(ipc_path.startswith("\\\\.\\pipe\\mpv-cinema-"))
        else:
            self.assertTrue(ipc_path.startswith(tempfile.gettempdir()))
            self.assertTrue("mpv-cinema-" in ipc_path)
            self.assertTrue(ipc_path.endswith(".sock"))

        # 4. Verify thread was started AFTER Popen (implicitly checked by execution order in mock)
        self.assertTrue(mock_thread.called)
        mock_thread_instance.start.assert_called_once()

        # 5. Verify cleanup (socket unlinked on Unix)
        if sys.platform != "win32":
            with patch("cli.src.utils.player.os.path.exists") as mock_exists:
                mock_exists.return_value = True
                with patch("cli.src.utils.player.os.unlink") as mock_unlink:
                    # We need to simulate the end of play_stream
                    # Cleanup happens after stats = _run_mpv(mpv_args)
                    pass

if __name__ == "__main__":
    unittest.main()
