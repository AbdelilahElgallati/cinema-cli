#!/usr/bin/env bash
# Cinema CLI — Linux/macOS Setup Script
set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${CYAN}  [INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}  [ OK ]${NC}  $*"; }
warn()  { echo -e "${YELLOW}  [WARN]${NC}  $*"; }
error() { echo -e "${RED}  [ERR ]${NC}  $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo
echo "  ████████████████████████████████████████████████████████"
echo "  █                                                      █"
echo "  █          Cinema CLI  —  Linux / macOS Setup          █"
echo "  █                                                      █"
echo "  ████████████████████████████████████████████████████████"
echo

# ── Detect OS ─────────────────────────────────────────────────────────────────
OS="$(uname -s)"
case "$OS" in
    Darwin*) PLATFORM="macos" ;;
    Linux*)  PLATFORM="linux" ;;
    *)       PLATFORM="unknown" ;;
esac
info "Platform: $OS"

# ── Check Python ───────────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3.9 python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver=$("$candidate" --version 2>&1 | awk '{print $2}')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 9 ]; then
            PYTHON="$candidate"
            ok "Python $ver found ($candidate)"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    error "Python 3.9+ not found."
    if [ "$PLATFORM" = "macos" ]; then
        echo "  Install via Homebrew:  brew install python"
        echo "  Or download from:      https://www.python.org/downloads/"
    else
        echo "  Install via apt:       sudo apt install python3 python3-venv"
        echo "  Or via dnf:            sudo dnf install python3"
    fi
    exit 1
fi

# ── Check Node.js ─────────────────────────────────────────────────────────────
if ! command -v node >/dev/null 2>&1; then
    error "Node.js not found (required for the backend)."
    if [ "$PLATFORM" = "macos" ]; then
        echo "  Install via Homebrew:  brew install node"
    else
        echo "  Install via nvm:       https://github.com/nvm-sh/nvm"
        echo "  Or via apt:            sudo apt install nodejs npm"
    fi
    exit 1
fi

NODE_VERSION=$(node -e "process.stdout.write(process.version.slice(1))")
NODE_MAJOR=$(echo "$NODE_VERSION" | cut -d. -f1)
if [ "$NODE_MAJOR" -lt 18 ]; then
    error "Node.js 18+ is required. Found: v$NODE_VERSION"
    exit 1
fi
ok "Node.js v$NODE_VERSION found."

# ── Check optional tools ───────────────────────────────────────────────────────
for tool in mpv ffmpeg; do
    if command -v "$tool" >/dev/null 2>&1; then
        ok "$tool found."
    else
        warn "$tool not found."
        if [ "$PLATFORM" = "macos" ]; then
            echo "       brew install $tool"
        else
            echo "       sudo apt install $tool"
        fi
    fi
done

if command -v aria2c >/dev/null 2>&1; then
    ok "aria2c found (optional — faster downloads)."
else
    info "aria2c not found (optional — install for faster downloads)."
fi

echo

# ── Create virtual environment ─────────────────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"
if [ -f "$VENV_DIR/bin/python" ]; then
    ok "Virtual environment already exists at .venv"
else
    info "Creating Python virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
    ok "Virtual environment created."
fi

# ── Install Python dependencies ────────────────────────────────────────────────
info "Installing Python requirements..."
"$VENV_DIR/bin/pip" install --upgrade pip --quiet
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/cli/requirements.txt" --quiet
ok "Python packages installed."

# ── Install Node.js backend dependencies ──────────────────────────────────────
info "Installing backend (Node.js) packages..."
pushd "$SCRIPT_DIR/backend" >/dev/null
npm install --silent
popd >/dev/null
ok "Backend Node packages installed."

# ── Create .env if missing ─────────────────────────────────────────────────────
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    if [ -f "$SCRIPT_DIR/.env_example" ]; then
        cp "$SCRIPT_DIR/.env_example" "$SCRIPT_DIR/.env"
        ok ".env created from .env_example — edit it to add your API keys."
    else
        printf 'TMDB_API_KEY=\nPORT=3010\nBACKEND_URL=http://127.0.0.1:3010\nOPENSUBTITLES_API_KEY=\nDISABLE_CACHE=false\n' \
            > "$SCRIPT_DIR/.env"
        ok ".env stub created — add your TMDB_API_KEY."
    fi
else
    ok ".env already exists."
fi

# ── Create launcher script ─────────────────────────────────────────────────────
LAUNCHER="$SCRIPT_DIR/cinema"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
cd "\$(dirname "\$0")"
.venv/bin/python cli/main.py "\$@"
EOF
chmod +x "$LAUNCHER"
ok "Launcher created: ./cinema"

# ── Optional: create symlink in ~/.local/bin ───────────────────────────────────
LOCAL_BIN="$HOME/.local/bin"
if [ -d "$LOCAL_BIN" ]; then
    ln -sf "$LAUNCHER" "$LOCAL_BIN/cinema-cli" 2>/dev/null && \
        ok "Symlink created: cinema-cli (runnable from anywhere)" || \
        warn "Could not create symlink in $LOCAL_BIN (non-fatal)."
fi

# ── Run first-run wizard ───────────────────────────────────────────────────────
echo
echo "  ────────────────────────────────────────────────────────"
echo "   Starting first-run setup wizard..."
echo "  ────────────────────────────────────────────────────────"
echo
"$VENV_DIR/bin/python" "$SCRIPT_DIR/cli/main.py" --setup

echo
echo "  ════════════════════════════════════════════════════════"
echo "   Setup complete!"
echo
echo "   To start Cinema CLI, run:"
echo "       ./cinema"
if [ -d "$LOCAL_BIN" ]; then
    echo "   Or from anywhere:"
    echo "       cinema-cli"
fi
echo "  ════════════════════════════════════════════════════════"
echo
