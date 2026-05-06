import json
import os
import shutil
import time

def load_json_data(filepath, default=None, expected_type=None):
    """Load JSON data safely with fallback to backup file.

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
        
        # Recover from backup
        backup_path = f"{filepath}.bak"
        if os.path.exists(backup_path):
            try:
                with open(backup_path, encoding="utf-8") as f:
                    data = json.load(f)
                if expected_type is not None and not isinstance(data, expected_type):
                    return default
                # Recover
                save_json_data(filepath, data)
                return data
            except Exception:
                pass
        return default
    except Exception as e:
        from src.utils import app_logger

        app_logger.warning(f"Unexpected error loading {filepath}: {e}")
        return default


def save_json_data(filepath, data):
    """Save JSON data atomically with retries for Windows locks."""
    tmp_path = f"{filepath}.tmp"
    backup_path = f"{filepath}.bak"
    
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
    except OSError:
        pass

    # Make backup of valid existing file
    if os.path.exists(filepath):
        try:
            # We don't want to copy a broken file, but we trust the runtime here.
            # Shutil.copyfile is not atomic but it's safe enough for backup
            shutil.copyfile(filepath, backup_path)
        except OSError:
            pass

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        # Try atomic replace with retries (Windows file locks)
        for attempt in range(5):
            try:
                os.replace(tmp_path, filepath)
                return
            except PermissionError:
                time.sleep(0.1)  # Wait for lock to clear
            except OSError as e:
                # Other OS errors might be transient or fatal, but try fallback
                if attempt == 4:
                    raise e
                time.sleep(0.1)

        # Fallback to copy & delete if replace failed repeatedly
        shutil.copyfile(tmp_path, filepath)
        os.remove(tmp_path)
    except Exception as e:
        try:
            from src.utils import app_logger
            app_logger.error(f"Error saving {filepath}: {e}")
        except Exception:
            pass
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
