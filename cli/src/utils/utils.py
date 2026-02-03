import re


def sanitize_filename(name):
    """Sanitize string to be safe for filenames"""
    return (
        "".join(c for c in name if c.isalnum() or c in " _-").strip().replace(" ", "_")
    )


def generate_filename(template, title, meta=None, source=None):
    """
    Generate a filename based on a template and metadata.
    Supported tokens: {title}, {year}, {season}, {episode}, {quality}, {provider}
    """
    meta = meta or {}
    source = source or {}

    # For TV episodes, extract just the series name (remove "- Episode X - Description")
    display_title = title
    if meta.get("type") == "tv":
        # Match pattern like "Show Name S01E05 - Episode Name"
        m = re.match(r"^(.*?)\s+S\d{1,2}E\d{1,2}\s*(?:-.*)?$", title)
        if m:
            display_title = m.group(1)

    safe_title = sanitize_filename(display_title)

    # Prepare replacements
    replacements = {
        "{title}": safe_title,
        "{year}": str(meta.get("year") or ""),
        "{season}": f"{meta.get('season', 0):02d}",
        "{episode}": f"{meta.get('episode', 0):02d}",
        "{quality}": source.get("quality", "unknown"),
        "{provider}": source.get("provider", "unknown"),
    }

    filename = template
    for k, v in replacements.items():
        filename = filename.replace(k, v)

    # Clean up artifacts from missing data
    # e.g. "Movie..mp4" -> "Movie.mp4"
    filename = re.sub(r"\.{2,}", ".", filename)
    filename = filename.replace("()", "")
    filename = filename.strip(" .-_")

    # Ensure extension
    if not filename.endswith(".mp4"):
        filename += ".mp4"

    return filename
