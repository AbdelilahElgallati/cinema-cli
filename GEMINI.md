# GEMINI.md — Cinema CLI Audit Rules & Context

> This file is the authoritative reference for all audit and fix sessions on the `cinema-cli` project.
> Read this file **fully** before touching a single line of code.

---

## 🗂️ Project Overview

**Name:** Cinema CLI  
**Repo:** https://github.com/AbdelilahElgallati/cinema-cli  
**Type:** Terminal-based TUI application for streaming and downloading movies & TV shows  
**Architecture:** Hybrid — Python TUI (frontend/client) + Node.js scraper (backend/proxy)

```
cinema-cli/
├── backend/          # Node.js scraper proxy (runs on localhost:3010)
├── cli/              # Python TUI (Textual or curses-based)
│   ├── main.py       # Entrypoint
│   ├── ui/           # Screen/panel components
│   ├── services/     # API clients (TMDB, OpenSubtitles, backend proxy)
│   ├── config/       # Settings, theme management
│   ├── downloads/    # Download manager logic
│   └── utils/        # Helpers (filesystem, subprocess, notifications)
├── setup.bat         # Windows installer
├── setup.sh          # Linux/macOS installer
├── .env_example      # Environment variable template
└── README.md
```

---

## 🛠️ Tech Stack

### Python TUI (cli/)
| Layer | Technology |
|---|---|
| Language | Python 3.9+ |
| TUI Framework | Likely Textual or curses / Rich |
| HTTP Client | requests / httpx |
| Config | python-dotenv + JSON |
| Subprocess | subprocess / shutil for mpv, ffmpeg, yt-dlp |
| Notifications | plyer or platform-native |
| Code Style | black (enforced per CONTRIBUTING.md) |

### Node.js Backend (backend/)
| Layer | Technology |
|---|---|
| Language | JavaScript (Node.js 18+) |
| Framework | Express.js (assumed) |
| HTTP Client | axios / node-fetch |
| Purpose | Scrapes streaming links from public sources |
| Port | localhost:3010 |

### External Dependencies
| Tool | Role |
|---|---|
| mpv | Video playback |
| ffmpeg | Subtitle/audio/video processing |
| yt-dlp | Stream downloads |
| aria2c | Optional faster downloads |
| TMDB API | Movie/TV metadata, search, trending |
| OpenSubtitles API | Subtitle fetching |

---

## ⚙️ Environment Variables

From `.env_example`:
```
TMDB_API_KEY=         # Required — TMDB v3 API key
OPENSUBTITLES_API_KEY= # Optional — for subtitle fallback
```

**Rules for audit:**
- Never hardcode API keys in source code
- Never commit real keys to the repo
- `.env` must be in `.gitignore`
- Always check `.env_example` is kept in sync with actual usage

---

## 📐 Code Quality Standards

### Python (cli/)
1. **Style:** `black` formatting is required. Check all files comply.
2. **Type hints:** All function signatures should have type annotations.
3. **Docstrings:** All public functions and classes must have docstrings.
4. **Error handling:** All subprocess calls and HTTP requests must be wrapped in try/except with meaningful user-facing messages.
5. **No bare `except:`** — always catch specific exceptions.
6. **No `print()` for UI output** — use the TUI framework's rendering layer.
7. **Constants:** No magic strings/numbers. Use named constants or config values.
8. **Logging:** Use Python `logging` module, not `print()` for debug output.

### JavaScript (backend/)
1. **Style:** Consistent indentation (2 spaces), semicolons consistent.
2. **Error handling:** All async operations must handle promise rejections.
3. **No `console.log` left in production paths** — use a logger or remove.
4. **Environment variables:** All config from `process.env`, never hardcoded.
5. **Input validation:** Validate and sanitize all incoming query parameters.

---

## 🔍 Audit Scope & Categories

For each module, investigate and report findings under these categories:

| Category | What to check |
|---|---|
| **Security** | API key exposure, path traversal, input injection, unsafe subprocess calls |
| **Error Handling** | Unhandled exceptions, missing try/catch, silent failures |
| **Correctness** | Logic bugs, wrong conditions, off-by-one, state machine violations |
| **Data Integrity** | Config corruption, partial writes, missing validation |
| **Performance** | Blocking calls in UI thread, missing caches, redundant API calls |
| **UX / Completeness** | Stub/mock responses, features listed in README but not implemented, broken keyboard shortcuts |
| **Dependency & Setup** | Missing dependency checks, broken setup scripts, version mismatches |
| **Code Quality** | Style violations, missing types, dead code, duplicated logic |

---

## 🚦 Severity Levels

Use EXACTLY these labels in all findings tables:

| Emoji | Label | Meaning |
|---|---|---|
| 🔴 | Critical | App crash, data loss, key exposure, complete feature broken |
| 🟠 | High | Major bug, security risk, significant UX failure |
| 🟡 | Medium | Logic error, incomplete feature, missing validation |
| 🟢 | Low | Style, cosmetic, minor improvement |

---

## ⚠️ Audit Rules (MUST follow)

1. **Read every file fully** before reporting findings on it. Do not infer from filenames alone.
2. **List findings in a table** per module with: `#`, `Severity`, `Category`, `Description`, `File + Line`, `Suggested Fix`.
3. **Do not fix while auditing** — audit first, report everything, then fix in a separate pass.
4. **Quote exact line numbers** where the issue is found.
5. **Mark stubs and mocks explicitly** as `[NOT IMPLEMENTED]` in the description.
6. **Cross-reference** — if a bug in `backend/` breaks something in `cli/`, note both.
7. **Check the setup scripts** (`setup.sh`, `setup.bat`) as first-class code, not afterthoughts.
8. **Check `.env_example`** — all variables used in code must be documented there.
9. **Report the README** — if a feature is documented but not implemented, that is a finding.
10. **Do not skip low-severity items** — report everything you find.

---

## 🔒 Security Checklist (check all of these)

- [ ] Are TMDB / OpenSubtitles API keys ever logged or printed to the terminal?
- [ ] Does the Node.js backend validate query parameters (e.g., movie ID, search query) before using them in URLs?
- [ ] Does any subprocess call use unsanitized user input (shell injection risk)?
- [ ] Are download paths validated to prevent path traversal (`../../etc/passwd`)?
- [ ] Is there any hardcoded URL, token, or credential in the codebase?
- [ ] Does the health check expose sensitive system information?
- [ ] Is the backend proxy accessible only on localhost, or could it be exposed externally?
- [ ] Are temporary files cleaned up after use?

---

## 🧩 Feature Completeness Checklist

Based on README, verify each feature is actually implemented end-to-end:

| Feature | Component | Check |
|---|---|---|
| Search (movies/TV) | cli/ + backend/ | Verify search → TMDB → display flow |
| Trending / Popular | cli/ + TMDB API | Verify category browsing |
| Genre browsing | cli/ | Verify genre filter UI |
| Stream (mpv playback) | cli/ subprocess | Verify stream URL → mpv launch |
| Quality selection | cli/ | Verify 4K→360p selection menu |
| Background downloads | cli/ + yt-dlp | Verify async download manager |
| Resume downloads | cli/ | Verify partial file resume |
| Batch downloads | cli/ | Verify `D` key batch flow |
| Multi-language subtitles | cli/ + OpenSubtitles | Verify subtitle fetch + mpv pass |
| Watch Later list | cli/ config | Verify add/remove/display |
| Favorites | cli/ config | Verify persistence across sessions |
| Local library scan | cli/ | Verify folder scan + resume |
| 9 themes | cli/ config | Verify all 9 themes render correctly |
| Settings backup export/import | cli/ | Verify JSON export/import |
| Desktop notifications | cli/ | Verify download-complete notification |
| Health check | cli/ startup | Verify missing-tool detection |
| `--version`, `--help`, `--setup` flags | cli/main.py | Verify all CLI flags work |

---

## 📋 Modules to Audit (in order)

### PHASE 1 — Core Entry & Setup
1. `setup.sh` and `setup.bat`
2. `cli/main.py`
3. `.env_example` vs actual env usage
4. `cli/config/` (settings, themes)

### PHASE 2 — Backend (Node.js Scraper)
5. `backend/` — Express server, routes, scrapers
6. Inter-process communication (how Python starts Node.js)

### PHASE 3 — CLI Services
7. `cli/services/` — TMDB client
8. `cli/services/` — OpenSubtitles client
9. `cli/services/` — Backend proxy client (localhost:3010)

### PHASE 4 — Core Features
10. `cli/downloads/` — Download manager
11. Stream/playback logic (mpv subprocess)
12. Subtitle pipeline
13. Watch Later / Favorites persistence

### PHASE 5 — UI Layer
14. `cli/ui/` — All screens/panels
15. Keyboard shortcut handling
16. Theme system

### PHASE 6 — Cross-cutting
17. Error handling consistency across all modules
18. Logging strategy
19. Dependency validation (health check)

---

## 📊 Required Output Format

### Per-Module Output

```
MODULE: <path>
Files read: <list all files read>

Findings:

| # | Severity | Category | Description | File + Line | Suggested Fix |
|---|---|---|---|---|---|
| 1 | 🟠 High | Security | ... | file.py:42 | ... |

Summary: X critical, X high, X medium, X low findings.
```

### End-of-Audit Output

Produce three tables:

1. **GLOBAL AUDIT SUMMARY TABLE** — all findings from all modules in one flat table, sorted by severity
2. **PRIORITY FIX LIST** — top 10 most impactful fixes, numbered
3. **FEATURE COMPLETENESS MATRIX** — each README feature vs actual implementation status (✅ / ⚠️ / ❌)

---

## 🚫 Do NOT Do These

- Do not fix anything during the audit phase
- Do not skip a file because it "looks fine" — read it
- Do not assume a feature works because it has a function name suggesting it does
- Do not report the same issue twice under different names
- Do not make up line numbers — only report lines you have actually read
- Do not suggest major architectural changes as "fixes" — suggest minimal targeted fixes
- Do not mark a feature ✅ unless you have verified the full end-to-end path

---

## ✅ Pre-Fix Checklist (before implementing any fix)

Before changing ANY file:
1. Re-read the full file you are about to change
2. Apply the **minimum change** needed — do not refactor unrelated code
3. Show a clear `before / after` diff for every change
4. After each fix, confirm: what file changed, what line changed, what the impact is
5. If a fix requires a new file, create it fully (no stubs)
6. If a fix is blocked by something not yet implemented, mark it `[BLOCKED — reason]` and move on
7. Verify that new imports do not create circular dependencies

---

## 📝 Notes on This Project's Special Characteristics

- **Dual-runtime:** Python and Node.js must both start correctly. Audit the startup sequence.
- **External tool dependency:** mpv, ffmpeg, yt-dlp, aria2c must be validated at startup. Check if missing tools cause graceful degradation or hard crashes.
- **Cross-platform:** Windows (`.bat`) and Linux/macOS (`.sh`) setup paths must both be audited.
- **Persistent state:** Watch Later, Favorites, and Downloads state is stored locally (likely JSON). Check for race conditions, corruption on crash, and migration strategy.
- **Network resilience:** TMDB API and the Node.js scraper can fail. Every network call must have a timeout and error state in the UI.
- **No authentication system** — this is a local tool, but API keys must still be protected.