#!/bin/bash
# postinstall.sh — runs once after plugininstall.pl extracts the plugin.
#
# Two jobs:
#   1. Make sure the CGI is executable. plugininstall.pl's chmod step runs
#      `find` from /root and silently fails as the loxberry user, leaving
#      index.cgi at 0644 — Apache then serves 500 on the plugin page.
#   2. Install pymammotion from pip. It's a pure-Python library with no
#      Debian package; the daemon refuses to start without it. We install
#      into /opt/loxberry/data/plugins/mammotion-mower/venv so the plugin's
#      Python deps are isolated from the system site-packages.

set -eu

PLUGINNAME="mammotion-mower"
LBHOMEDIR="${LBHOMEDIR:-/opt/loxberry}"
HTMLAUTHDIR="$LBHOMEDIR/webfrontend/htmlauth/plugins/$PLUGINNAME"
DATADIR="$LBHOMEDIR/data/plugins/$PLUGINNAME"
VENVDIR="$DATADIR/venv"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"

echo "[postinstall] fixing CGI permissions"
if [ -f "$HTMLAUTHDIR/index.cgi" ]; then
    chmod 0755 "$HTMLAUTHDIR/index.cgi"
    chown loxberry:loxberry "$HTMLAUTHDIR/index.cgi" 2>/dev/null || true
fi

echo "[postinstall] preparing data directory"
mkdir -p "$DATADIR"
chown -R loxberry:loxberry "$DATADIR" 2>/dev/null || true

echo "[postinstall] creating Python virtualenv at $VENVDIR"
if [ ! -x "$VENVDIR/bin/python3" ]; then
    su loxberry -c "$PYTHON_BIN -m venv --system-site-packages '$VENVDIR'" || {
        echo "[postinstall] venv creation failed — falling back to user site-packages" >&2
    }
fi

echo "[postinstall] installing pymammotion into the venv"
if [ -x "$VENVDIR/bin/pip" ]; then
    su loxberry -c "$VENVDIR/bin/pip install --upgrade pip" || true
    su loxberry -c "$VENVDIR/bin/pip install --upgrade pymammotion" || {
        echo "[postinstall] WARNING: pip install pymammotion failed." >&2
        echo "[postinstall] You can install it manually with:" >&2
        echo "[postinstall]   su loxberry -c '$VENVDIR/bin/pip install pymammotion'" >&2
    }
else
    echo "[postinstall] venv pip not available — trying user-level pip install" >&2
    su loxberry -c "$PYTHON_BIN -m pip install --user --upgrade pymammotion" || true
fi

echo "[postinstall] done"
exit 0
