import time
import uuid
from threading import Lock

from src.utils.storage import load_json_data, save_json_data


class QueueManager:
    """Manage download queue state and persistence."""

    def __init__(self, queue_file: str):
        self.queue_file = queue_file
        self._save_lock = Lock()

    def load_queue(self):
        queue = load_json_data(self.queue_file, default=[], expected_type=list) or []
        for task in queue:
            if task.get("status") == "downloading":
                task["status"] = "pending"
        return queue

    def save_queue(self, queue):
        with self._save_lock:
            save_json_data(self.queue_file, queue)

    def build_task(
        self,
        url,
        filename,
        title,
        subtitles=None,
        headers=None,
        meta=None,
        fallback_sources=None,
        api_params=None,
        preferred_sub_lang="ar",
        include_all_subs=True,
        preferred_sub_langs=None,
        fallback_sub_langs=None,
        quality=None,
        speed_limit_mb=0,
    ):
        return {
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
            "speed_limit_mb": speed_limit_mb,
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

    def retry_task(self, queue, task_id):
        for task in queue:
            if task.get("id") != task_id:
                continue
            task["status"] = "pending"
            task["progress"] = 0
            task["speed"] = "0 B/s"
            task["eta"] = "00:00"
            task["retries"] = 0
            task["error_log"] = "Manual retry triggered.\n"
            for key in ["_frag_current", "_frag_total", "_base_progress"]:
                task.pop(key, None)
            return True
        return False

    def remove_task(self, queue, task_id):
        before = len(queue)
        queue[:] = [task for task in queue if task.get("id") != task_id]
        return len(queue) != before

    def clear_completed(self, queue):
        before = len(queue)
        queue[:] = [task for task in queue if task.get("status") != "completed"]
        return len(queue) != before
