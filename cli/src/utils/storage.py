import json
import os


def load_json_data(filepath, default=None, expected_type=None):
    """Load JSON data safely.

    Args:
        filepath: JSON file path.
        default: Fallback value returned when file is missing/invalid.
                 Defaults to an empty list for backwards compatibility.
        expected_type: Optional type to enforce (e.g. dict or list).
    """
    if default is None:
        default = []

    if not os.path.exists(filepath):
        return default

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if expected_type is not None and not isinstance(data, expected_type):
            return default
        return data
    except Exception:
        return default


def save_json_data(filepath, data):
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, filepath)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        # Swallowing here for now as per original code; FIX H4 will address logging.
        pass
