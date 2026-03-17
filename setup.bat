@echo off
setlocal EnableDelayedExpansion
title Cinema CLI — Setup

echo.
echo  ████████████████████████████████████████████████████████
echo  █                                                      █
echo  █           Cinema CLI  —  Windows Setup               █
echo  █                                                      █
echo  ████████████████████████████████████████████████████████
echo.

:: ── Check Python ──────────────────────────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python not found.
    echo  Install Python 3.9+ from https://www.python.org/downloads/
    echo  Make sure to tick "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo  [OK] Python %PYVER% found.

:: ── Check Node.js ─────────────────────────────────────────────────────────────
where node >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Node.js not found.
    echo  Install Node.js 18+ from https://nodejs.org/
    pause
    exit /b 1
)
for /f "tokens=1" %%v in ('node --version 2^>^&1') do set NODEVER=%%v
echo  [OK] Node.js %NODEVER% found.

:: ── Warn about optional tools ────────────────────────────────────────────────
where mpv >nul 2>&1
if errorlevel 1 (
    echo  [WARN] mpv not found  ^(required for playback^)
    echo         Install: winget install mpv
    echo                  OR download from https://mpv.io/installation/
) else (
    echo  [OK] mpv found.
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo  [WARN] ffmpeg not found  ^(required for downloads^)
    echo         Install: winget install ffmpeg
) else (
    echo  [OK] ffmpeg found.
)

where aria2c >nul 2>&1
if errorlevel 1 (
    echo  [INFO] aria2c not found  ^(optional — speeds up downloads^)
    echo         Install: winget install aria2
) else (
    echo  [OK] aria2c found.
)

echo.

:: ── Create virtual environment ────────────────────────────────────────────────
set VENV_DIR=%~dp0.venv
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo  [OK] Virtual environment already exists at .venv
) else (
    echo  Creating Python virtual environment...
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo  [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  [OK] Virtual environment created.
)

:: ── Install Python requirements ───────────────────────────────────────────────
echo  Installing Python requirements...
"%VENV_DIR%\Scripts\pip.exe" install --upgrade pip --quiet
"%VENV_DIR%\Scripts\pip.exe" install -r "%~dp0cli\requirements.txt" --quiet
if errorlevel 1 (
    echo  [ERROR] pip install failed.
    pause
    exit /b 1
)
echo  [OK] Python packages installed.

:: ── Install Node.js backend dependencies ─────────────────────────────────────
echo  Installing backend (Node.js) packages...
pushd "%~dp0backend"
call npm install --silent
if errorlevel 1 (
    echo  [ERROR] npm install failed.
    popd
    pause
    exit /b 1
)
popd
echo  [OK] Backend Node packages installed.

:: ── Copy .env if it doesn't exist ────────────────────────────────────────────
if not exist "%~dp0.env" (
    if exist "%~dp0.env_example" (
        copy "%~dp0.env_example" "%~dp0.env" >nul
        echo  [OK] .env created from .env_example  — edit it to add your API keys.
    ) else (
        echo  TMDB_API_KEY=> "%~dp0.env"
        echo  BACKEND_URL=http://localhost:3010>> "%~dp0.env"
        echo  OPENSUBTITLES_API_KEY=>> "%~dp0.env"
        echo  [OK] .env stub created  — add your TMDB_API_KEY.
    )
) else (
    echo  [OK] .env already exists.
)

:: ── Create launcher script ────────────────────────────────────────────────────
set LAUNCHER=%~dp0cinema.bat
(
    echo @echo off
    echo cd /d "%~dp0"
    echo "%VENV_DIR%\Scripts\python.exe" cli\main.py %%*
) > "%LAUNCHER%"
echo  [OK] Launcher created: cinema.bat

:: ── Run first-run wizard ──────────────────────────────────────────────────────
echo.
echo  ────────────────────────────────────────────────────────
echo   Starting first-run setup wizard...
echo  ────────────────────────────────────────────────────────
echo.
"%VENV_DIR%\Scripts\python.exe" cli\main.py --setup

echo.
echo  ════════════════════════════════════════════════════════
echo   Setup complete!
echo.
echo   To start Cinema CLI, run:
echo       cinema.bat
echo   Or:
echo       .venv\Scripts\python.exe cli\main.py
echo  ════════════════════════════════════════════════════════
echo.
pause
