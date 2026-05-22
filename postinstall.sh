#!/bin/bash
# postinstall.sh — runs once after plugininstall.pl extracts the plugin.
#
# Jobs:
#   1. Make sure the CGI is executable (LoxBerry's chmod step sometimes fails).
#   2. Provide a Python ≥ 3.13 interpreter. pymammotion uses Python 3.12+
#      f-string syntax (PEP 701) that does not parse on Python 3.11, which
#      is the version shipped by LoxBerry 3.x (Debian Bookworm). When the
#      system python is too old, download a relocatable Python 3.13 from
#      Astral's python-build-standalone — a ~30 MB tarball, no system deps.
#   3. Create a venv with that interpreter and pip-install pymammotion +
#      paho-mqtt into it. The daemon's wrapper picks up the venv python
#      automatically.

set -u

PLUGINNAME="mammotion-mower"
LBHOMEDIR="${LBHOMEDIR:-/opt/loxberry}"
HTMLAUTHDIR="$LBHOMEDIR/webfrontend/htmlauth/plugins/$PLUGINNAME"
DATADIR="$LBHOMEDIR/data/plugins/$PLUGINNAME"
VENVDIR="$DATADIR/venv"
STANDALONE_DIR="$DATADIR/python-standalone"

# python-build-standalone release pin — bump as needed.
PBS_TAG="20260510"
PBS_VERSION="3.13.13"

ME="$(id -un)"
echo "[postinstall] running as user: $ME"

run_as_loxberry() {
    # Run a command as the loxberry user. If we're already loxberry, fork
    # directly (su requires PAM and may fail in containerized contexts).
    if [ "$ME" = "loxberry" ]; then
        bash -c "$1"
    else
        su loxberry -s /bin/bash -c "$1"
    fi
}

# -------------------------------------------------------------- CGI ---
echo "[postinstall] fixing CGI permissions"
if [ -f "$HTMLAUTHDIR/index.cgi" ]; then
    chmod 0755 "$HTMLAUTHDIR/index.cgi" 2>&1 || true
    chown loxberry:loxberry "$HTMLAUTHDIR/index.cgi" 2>&1 || true
fi

# --------------------------------------------------------- data dir ---
echo "[postinstall] preparing data directory"
mkdir -p "$DATADIR" 2>&1 || true
chown -R loxberry:loxberry "$DATADIR" 2>&1 || true

# ------------------------------------------------- python interpreter ---
# Pick the best available Python: system if >= 3.12, otherwise standalone.
SYSTEM_PYTHON="/usr/bin/python3"
PYTHON_BIN=""

if [ -x "$SYSTEM_PYTHON" ]; then
    SYS_VER="$("$SYSTEM_PYTHON" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null || echo "0.0")"
    SYS_MAJOR="${SYS_VER%.*}"
    SYS_MINOR="${SYS_VER#*.}"
    echo "[postinstall] system python: $SYSTEM_PYTHON ($SYS_VER)"
    if [ "$SYS_MAJOR" = "3" ] && [ "$SYS_MINOR" -ge 12 ]; then
        PYTHON_BIN="$SYSTEM_PYTHON"
    fi
fi

if [ -z "$PYTHON_BIN" ]; then
    echo "[postinstall] system python is too old for pymammotion (needs 3.12+)"

    # Pick the right arch for python-build-standalone.
    ARCH="$(uname -m)"
    case "$ARCH" in
        x86_64)  PBS_ARCH="x86_64-unknown-linux-gnu" ;;
        aarch64) PBS_ARCH="aarch64-unknown-linux-gnu" ;;
        armv7l|armhf)
            echo "[postinstall] ERROR: 32-bit ARM (armv7l) is not supported by python-build-standalone." >&2
            echo "[postinstall] Please run this plugin on a 64-bit LoxBerry (RPi 4/5 with arm64 OS, or x86_64)." >&2
            exit 0
            ;;
        *)
            echo "[postinstall] ERROR: unsupported architecture: $ARCH" >&2
            exit 0
            ;;
    esac

    PBS_TARBALL="cpython-${PBS_VERSION}+${PBS_TAG}-${PBS_ARCH}-install_only_stripped.tar.gz"
    PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${PBS_TARBALL}"

    if [ ! -x "$STANDALONE_DIR/python/bin/python3" ]; then
        echo "[postinstall] downloading $PBS_TARBALL (~30 MB, one time)"
        rm -rf "$STANDALONE_DIR"
        mkdir -p "$STANDALONE_DIR"
        chown -R loxberry:loxberry "$STANDALONE_DIR" 2>&1 || true
        if ! run_as_loxberry "curl -fsSL --retry 3 --max-time 600 -o '$STANDALONE_DIR/python.tar.gz' '$PBS_URL'"; then
            echo "[postinstall] ERROR: download failed — check internet access" >&2
            exit 0
        fi
        echo "[postinstall] extracting tarball"
        run_as_loxberry "tar xzf '$STANDALONE_DIR/python.tar.gz' -C '$STANDALONE_DIR'"
        rm -f "$STANDALONE_DIR/python.tar.gz"
    fi

    if [ -x "$STANDALONE_DIR/python/bin/python3" ]; then
        PYTHON_BIN="$STANDALONE_DIR/python/bin/python3"
        echo "[postinstall] using standalone python: $PYTHON_BIN ($("$PYTHON_BIN" --version 2>&1))"
    else
        echo "[postinstall] ERROR: standalone python not executable after extract" >&2
        exit 0
    fi
fi

# --------------------------------------------------------------- venv ---
# Drop any stale incomplete venv from a previous failed install.
if [ -d "$VENVDIR" ] && [ ! -x "$VENVDIR/bin/python3" ]; then
    echo "[postinstall] removing stale incomplete venv at $VENVDIR"
    rm -rf "$VENVDIR" 2>&1 || true
fi

if [ ! -x "$VENVDIR/bin/python3" ]; then
    echo "[postinstall] creating venv at $VENVDIR (interpreter: $PYTHON_BIN)"
    # Standalone python ships its own pip, so --without-pip + ensurepip isn't
    # needed. The system python may need --system-site-packages for paho.
    if [[ "$PYTHON_BIN" == "$STANDALONE_DIR/"* ]]; then
        VENV_FLAGS=""
    else
        VENV_FLAGS="--system-site-packages"
    fi
    if run_as_loxberry "'$PYTHON_BIN' -m venv $VENV_FLAGS '$VENVDIR' 2>&1"; then
        echo "[postinstall] venv created"
    else
        echo "[postinstall] ERROR: venv creation failed" >&2
        exit 0
    fi
fi

# ---------------------------------------------------------- pymammotion ---
if [ -x "$VENVDIR/bin/pip" ]; then
    echo "[postinstall] installing pymammotion into the venv (this may take 1-3 min)"
    run_as_loxberry "'$VENVDIR/bin/pip' install --quiet --upgrade pip" || true
    if run_as_loxberry "'$VENVDIR/bin/pip' install --quiet --upgrade pymammotion paho-mqtt"; then
        echo "[postinstall] pymammotion installed in venv"
        # Confirm it actually imports — catches Python-version mismatches early.
        if run_as_loxberry "'$VENVDIR/bin/python3' -c 'import pymammotion; print(\"[postinstall] pymammotion import OK\")'"; then
            :
        else
            echo "[postinstall] WARNING: pymammotion installed but import failed" >&2
        fi
    else
        echo "[postinstall] WARNING: pip install pymammotion failed." >&2
        echo "[postinstall] You can install it manually with:" >&2
        echo "[postinstall]   su loxberry -c '$VENVDIR/bin/pip install pymammotion paho-mqtt'" >&2
    fi
else
    echo "[postinstall] ERROR: venv pip not available — abort" >&2
fi

echo "[postinstall] done"
exit 0
