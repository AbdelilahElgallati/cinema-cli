@echo off
setlocal EnableDelayedExpansion
title Cinema CLI — Setup

echo.
echo  ========================================================
echo             Cinema CLI  —  Windows Setup
echo  ========================================================
echo.

:: ── Check Python ──────────────────────────────────────────────────────────────
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Python not found in PATH.
    echo  Install Python 3.9+ from https://www.python.org/downloads/
    echo  Make sure to tick "Add Python to PATH" during installation.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
for /f "tokens=1,2 delims=." %%a in ("%PYVER%") do (
    set PY_MAJOR=%%a
    set PY_MINOR=%%b
)
if %PY_MAJOR% LSS 3 (
    echo  [ERROR] Python 3.9+ is required. Found: %PYVER%
    pause & exit /b 1
)
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 9 (
    echo  [ERROR] Python 3.9+ is required. Found: %PYVER%
    pause & exit /b 1
)
echo  [OK] Python %PYVER% found.

:: ── Check Node.js ─────────────────────────────────────────────────────────────
where node >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Node.js not found in PATH.
    echo  Install Node.js 18+ from https://nodejs.org/
    pause
    exit /b 1
)
for /f "tokens=1" %%v in ('node --version 2^>^&1') do set NODEVER=%%v
set NODE_MAJOR_VER=%NODEVER:v=%
for /f "tokens=1 delims=." %%m in ("%NODE_MAJOR_VER%") do set NODE_MAJOR=%%m
if %NODE_MAJOR% LSS 18 (
    echo  [ERROR] Node.js 18+ is required. Found: %NODEVER%
    pause & exit /b 1
)
echo  [OK] Node.js %NODEVER% found.

:: ── Create virtual environment ────────────────────────────────────────────────
set VENV_DIR=%~dp0.venv
if exist "%VENV_DIR%\Scripts\python.exe" (
    echo  [OK] Virtual environment already exists at .venv
) else (
    echo  Creating Python virtual environment...
    python -m venv "%VENV_DIR%"
    if %ERRORLEVEL% neq 0 (
        echo  [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo  [OK] Virtual environment created.
)

:: ── Install Python requirements ───────────────────────────────────────────────
echo  Installing Python requirements...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip --quiet
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Failed to upgrade pip.
    pause
    exit /b 1
)
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%~dp0cli\requirements.txt" --quiet
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] pip install failed. Please check the logs above.
    pause
    exit /b 1
)
echo  [OK] Python packages installed.

:: ── Install Node.js backend dependencies ─────────────────────────────────────
where npm >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] npm not found. Node.js installation might be corrupt.
    pause
    exit /b 1
)

echo  Checking backend (Node.js) dependencies...
pushd "%~dp0backend"
call npm install --silent
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] npm install failed. Backend might not start correctly.
    popd
    pause
    exit /b 1
)
echo  [OK] Backend Node packages ready.
popd

:: ── Copy .env if it doesn't exist ────────────────────────────────────────────
if not exist "%~dp0.env" (
    if exist "%~dp0.env_example" (
        copy "%~dp0.env_example" "%~dp0.env" >nul
        echo  [OK] .env created from .env_example.
    ) else (
        echo TMDB_API_KEY=> "%~dp0.env"
        echo PORT=3010>> "%~dp0.env"
        echo BACKEND_URL=http://127.0.0.1:3010>> "%~dp0.env"
        echo OPENSUBTITLES_API_KEY=>> "%~dp0.env"
        echo DISABLE_CACHE=false>> "%~dp0.env"
        echo  [OK] .env stub created.
    )
) else (
    echo  [OK] .env already exists.
)

:: ── Create launcher script ────────────────────────────────────────────────────
set LAUNCHER=%~dp0cinema.bat
(
    echo @echo off
    echo cd /d "%%~dp0"
    echo "%%~dp0.venv\Scripts\python.exe" cli\main.py %%*
) > "%LAUNCHER%"
echo  [OK] Launcher created: cinema.bat

:: ── Run first-run wizard ──────────────────────────────────────────────────────
echo.
echo  --------------------------------------------------------
echo   Starting first-run setup wizard...
echo  --------------------------------------------------------
echo.
"%VENV_DIR%\Scripts\python.exe" cli\main.py --setup
if %ERRORLEVEL% neq 0 (
    echo  [ERROR] Setup wizard encountered an error.
    pause
    exit /b 1
)

echo.
echo  ========================================================
echo   Setup complete!
echo.
echo   To start Cinema CLI, run:
echo       cinema.bat
echo   Or:
echo       .venv\Scripts\python.exe cli\main.py
echo  ========================================================
echo.
pause