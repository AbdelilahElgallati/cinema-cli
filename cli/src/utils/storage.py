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
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        if expected_type is not None and not isinstance(data, expected_type):
            return default
        return data
    except (json.JSONDecodeError, OSError) as e:
        from src.utils import app_logger

        app_logger.debug(f"Error loading {filepath}: {e}")
        return default
    except Exception as e:
        from src.utils import app_logger

        app_logger.warning(f"Unexpected error loading {filepath}: {e}")
        return default


def save_json_data(filepath, data):
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, filepath)
    except (json.JSONDecodeError, OSError) as e:
        from src.utils import app_logger

        app_logger.error(f"Error saving {filepath}: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    except Exception as e:
        from src.utils import app_logger

        app_logger.error(f"Unexpected error saving {filepath}: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
