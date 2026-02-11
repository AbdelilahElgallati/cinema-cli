import os
import requests
from src.config import console
from src.ui.theme import theme


class SubtitleManager:
    def __init__(self, temp_dir=".download_temp"):
        self.temp_dir = os.path.join(os.getcwd(), temp_dir)
        os.makedirs(self.temp_dir, exist_ok=True)

    def get_subtitles(self, title, source_subtitles, match_data={}, preferred_langs=None, silent=False):
        """
        Tries to find subtitles for all preferred languages.
        Returns a list of local paths.
        """
        if preferred_langs is None:
            from src.core.settings import SettingsManager
            settings = SettingsManager()
            preferred_langs = settings.subtitle_languages
            
        collected_paths = []
        # Ensure Arabic and English are prioritized at the start if not already
        langs = list(preferred_langs)
        for prioritize in ["en", "ar"]:
            if prioritize in langs:
                langs.remove(prioritize)
            langs.insert(0, prioritize)
            
        for lang in langs:
            if not silent:
                console.print(f"[{theme.accent}]Searching for {lang} subtitles...[/{theme.accent}]")
            # 1. Check provided source subtitles
            candidates = [
                s for s in source_subtitles 
                if self._lang_match(s.get("lang"), lang)
            ]
            if candidates:
                sub = candidates[0]
                url = sub.get("url") or sub.get("file")
                if url:
                    path = self._download_sub(url, title, lang)
                    if path: 
                        if not silent:
                            console.print(f"[{theme.success}]Found {lang} in source.[/{theme.success}]")
                        collected_paths.append(path)
                        continue 
            
            # 2. OpenSubtitles fallback
            from src.utils.subtitles import fetch_subtitle
            search_title = match_data.get("series_name") or title
            res = fetch_subtitle(
                search_title, 
                year=match_data.get("year"), 
                season=match_data.get("season"), 
                episode=match_data.get("episode"),
                lang=lang
            )
            if res:
                content, ext = res
                filename = f"{self._sanitize(title)}_{lang}.{ext}"
                path = os.path.join(self.temp_dir, filename)
                try:
                    with open(path, "wb") as f:
                        f.write(content)
                    collected_paths.append(path)
                    if not silent:
                        console.print(f"[{theme.success}]Fetched {lang} from OpenSubtitles.[/{theme.success}]")
                except Exception:
                    pass
        
        # FINAL FALLBACK: If still no subtitles for preferred langs, take the first one available from source
        if not collected_paths and source_subtitles:
            if not silent:
                console.print(f"[{theme.warning}]No preferred subs found. Using first available...[/{theme.warning}]")
            sub = source_subtitles[0]
            url = sub.get("url") or sub.get("file")
            lang = sub.get("lang", "any")
            if url:
                path = self._download_sub(url, title, lang)
                if path:
                    collected_paths.append(path)
        
        return collected_paths

    def _lang_match(self, sub_lang, target_lang):
        if not sub_lang: return False
        sub_lang = sub_lang.lower()
        target_lang = target_lang.lower()
        # Handle labels like "Arabic (Full)", "English [CC]", etc.
        if target_lang in ["ar", "ara", "arabic"]:
            return any(x in sub_lang for x in ["ar", "ara", "arabic"])
        if target_lang in ["en", "eng", "english"]:
            return any(x in sub_lang for x in ["en", "eng", "english"])
        if target_lang in ["fr", "fre", "french"]:
            return any(x in sub_lang for x in ["fr", "fre", "french"])
        return target_lang in sub_lang

    def _download_sub(self, url, title, lang):
        try:
            ext = "srt"
            if ".vtt" in url: ext = "vtt"
            
            filename = f"{self._sanitize(title)}_{lang}.{ext}"
            path = os.path.join(self.temp_dir, filename)
            
            # Simple cache check?
            if os.path.exists(path): return path

            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                 with open(path, "wb") as f:
                     f.write(r.content)
                 return path
        except:
            pass
        return None

    def _sanitize(self, name):
        return "".join(c for c in name if c.isalnum() or c in "._-").strip()
