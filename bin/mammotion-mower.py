#!/usr/bin/env python3
"""LoxBerry daemon: bridge Mammotion robot mowers to local MQTT.

Architecture
============

  Mammotion cloud (Aliyun MQTT)
        │  pymammotion.client.MammotionClient
        ▼
  asyncio event loop in this process
        │  pull DeviceHandle.snapshot.raw every poll_interval_seconds
        ▼
  paho-mqtt → localhost:1883
        │  topics: <prefix>/<device_id>/<datapoint>
        ▼
  LoxBerry built-in MQTT Gateway (mqttgateway.pl)
        │  reads <lbpconfigdir>/mqtt_subscriptions.cfg via inotify
        ▼
  Loxone Miniserver Virtual Inputs

Inbound commands (when enable_commands=true):
  Subscribe to <prefix>/<device_id>/set/<command>
  Translate to MammotionClient.send_command_with_args(...).

Configuration is read from $LBPCONFIGDIR/default.json (overwritten on every
plugin install — that's the documented LoxBerry pattern; the user re-enters
credentials after each upgrade).
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import re
import signal
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# LoxBerry-provided paths (fall back to local relative dirs for dev runs)
# ---------------------------------------------------------------------------
PLUGIN_NAME = "mammotion-mower"
BASE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = Path(os.environ.get("LBPCONFIGDIR") or BASE_DIR.parent / "config")
LOG_DIR = Path(os.environ.get("LBPLOGDIR") or BASE_DIR.parent / "logs")
DATA_DIR = Path(os.environ.get("LBPDATADIR") or BASE_DIR.parent / "data")
SYS_CFG_DIR = Path(os.environ.get("LBSCONFIG") or "/opt/loxberry/config/system")

CONFIG_FILE = CONFIG_DIR / "default.json"
LOG_FILE = LOG_DIR / f"{PLUGIN_NAME}.log"
PID_FILE = LOG_DIR / f"{PLUGIN_NAME}.pid"

for d in (CONFIG_DIR, LOG_DIR, DATA_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Pidfile ownership
# ---------------------------------------------------------------------------
# The bash wrapper writes the pidfile right after fork. We register an atexit
# cleanup so EVERY exit path — clean return, soft-exit, unhandled exception,
# SIGTERM — removes it. Without this, a daemon that exits on its own (e.g. 6
# failed login retries) leaves a stale pidfile and the CGI shows
# "stopped (stale pidfile)" forever until the next manual restart.
def _remove_pidfile_if_ours() -> None:
    try:
        if not PID_FILE.exists():
            return
        try:
            recorded = int(PID_FILE.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            return
        if recorded == os.getpid():
            PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


atexit.register(_remove_pidfile_if_ours)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("mammotion-mower")

# Quiet down noisy library loggers (paho heartbeats, aiomqtt reconnects).
for noisy in ("paho", "paho.mqtt", "aiomqtt", "aliyun-iot-linkkit", "pymammotion.transport"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Topic / id sanitisation
# ---------------------------------------------------------------------------
TOPIC_SEGMENT_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def safe_segment(value: Any, fallback: str = "device") -> str:
    """Coerce *value* into something safe to embed in an MQTT topic."""
    cleaned = TOPIC_SEGMENT_RE.sub("_", str(value if value is not None else "")).strip("_")
    return cleaned.lower() or fallback


def redact_url(url: str) -> str:
    """Strip query string and userinfo so URLs are safe to log."""
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://{host}{port}{parts.path}"
    except Exception:  # noqa: BLE001
        return "<unparseable-url>"


# ---------------------------------------------------------------------------
# Config loader (with whitespace stripping on credentials)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "account_email": "",
    "account_password": "",
    "poll_interval_seconds": 60,
    "command_settle_seconds": 5,
    "use_loxberry_mqtt": True,
    "mqtt_host": "localhost",
    "mqtt_port": 1883,
    "mqtt_username": "",
    "mqtt_password": "",
    "mqtt_topic_prefix": "mammotion",
    "register_mqtt_subscription": True,
    "enable_commands": True,
    "command_topic_suffix": "set",
    "ha_version_tag": "0.5.47",
    "debug": False,
}


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.is_file():
        try:
            with CONFIG_FILE.open(encoding="utf-8-sig") as fh:
                cfg.update(json.load(fh))
        except (OSError, json.JSONDecodeError) as e:
            log.error("Failed to read %s: %s — using defaults", CONFIG_FILE, e)

    # Strip whitespace on credentials — password managers / autofill commonly
    # paste a trailing space which silently breaks the upstream login.
    for key in ("account_email", "account_password", "mqtt_username", "mqtt_password"):
        if isinstance(cfg.get(key), str):
            cfg[key] = cfg[key].strip()

    # Sanitise topic prefix — MQTT topics cannot contain # + / or whitespace.
    prefix = str(cfg.get("mqtt_topic_prefix") or "mammotion").strip().strip("/")
    cfg["mqtt_topic_prefix"] = TOPIC_SEGMENT_RE.sub("_", prefix) or "mammotion"

    # Clamp polling interval to a sane range.
    try:
        cfg["poll_interval_seconds"] = max(10, min(3600, int(cfg.get("poll_interval_seconds", 60))))
    except (TypeError, ValueError):
        cfg["poll_interval_seconds"] = 60

    # File contains a plaintext password — keep mode 0600 on every load.
    try:
        os.chmod(CONFIG_FILE, 0o600)
    except OSError:
        pass

    if cfg.get("debug"):
        logging.getLogger().setLevel(logging.DEBUG)
        log.setLevel(logging.DEBUG)

    return cfg


def load_loxberry_mqtt_creds() -> dict[str, Any] | None:
    """Read broker credentials from LoxBerry's general.json.

    Returns None when LoxBerry is not present or the file lacks an Mqtt section
    (i.e. the daemon is running standalone — fall back to manual config).
    """
    path = SYS_CFG_DIR / "general.json"
    try:
        with path.open(encoding="utf-8-sig") as fh:
            mqtt = json.load(fh).get("Mqtt") or {}
    except (OSError, json.JSONDecodeError):
        return None
    if not (mqtt.get("Brokerhost") and mqtt.get("Brokerport")):
        return None
    return {
        "host": str(mqtt["Brokerhost"]),
        "port": int(mqtt["Brokerport"]),
        "username": mqtt.get("Brokeruser") or "",
        "password": mqtt.get("Brokerpass") or "",
    }


def register_mqtt_subscription(prefix: str) -> None:
    """Drop a single-line mqtt_subscriptions.cfg in LBPCONFIGDIR so the
    built-in MQTT Gateway relays <prefix>/# to the Miniserver."""
    if not CONFIG_DIR.exists():
        return
    target = CONFIG_DIR / "mqtt_subscriptions.cfg"
    body = f"{prefix}/#\n"
    if target.exists() and target.read_text(encoding="utf-8") == body:
        return  # already up to date — avoid retriggering inotify
    target.write_text(body, encoding="utf-8")


# ---------------------------------------------------------------------------
# MQTT publisher (paho — 1.x and 2.x compatible)
# ---------------------------------------------------------------------------
import paho.mqtt.client as mqtt_client_mod  # noqa: E402

_PUBLISHED_CACHE: dict[str, str] = {}


def build_mqtt_client(cfg: dict[str, Any]) -> "mqtt_client_mod.Client":
    if cfg["use_loxberry_mqtt"]:
        discovered = load_loxberry_mqtt_creds()
        if discovered:
            host, port = discovered["host"], discovered["port"]
            user, password = discovered["username"], discovered["password"]
            log.info("Using auto-discovered LoxBerry MQTT broker at %s:%s", host, port)
        else:
            host, port = cfg["mqtt_host"], int(cfg["mqtt_port"])
            user, password = cfg["mqtt_username"], cfg["mqtt_password"]
            log.warning(
                "use_loxberry_mqtt is true but LoxBerry general.json has no Mqtt section — "
                "falling back to manual broker config (%s:%s)",
                host,
                port,
            )
    else:
        host, port = cfg["mqtt_host"], int(cfg["mqtt_port"])
        user, password = cfg["mqtt_username"], cfg["mqtt_password"]
        log.info("Using manually-configured MQTT broker at %s:%s", host, port)

    client_kwargs: dict[str, Any] = {"client_id": f"{PLUGIN_NAME}-{os.getpid()}"}
    if hasattr(mqtt_client_mod, "CallbackAPIVersion"):
        client_kwargs["callback_api_version"] = mqtt_client_mod.CallbackAPIVersion.VERSION2
    client = mqtt_client_mod.Client(**client_kwargs)

    if user:
        client.username_pw_set(user, password or None)

    will_topic = f"{cfg['mqtt_topic_prefix']}/_status"
    client.will_set(will_topic, payload="offline", retain=True)

    def _on_connect(c, _userdata, _flags, reason_code, _properties=None):
        rc = getattr(reason_code, "value", reason_code)
        if rc == 0:
            log.info("MQTT connected to %s:%s", host, port)
            _PUBLISHED_CACHE.clear()
            c.publish(will_topic, "online", retain=True)
        else:
            log.error("MQTT connect failed: rc=%s", rc)

    def _on_disconnect(_c, _userdata, *args, **_kw):
        log.warning("MQTT disconnected (args=%s)", args)

    client.on_connect = _on_connect
    client.on_disconnect = _on_disconnect

    client.connect_async(host, port, keepalive=60)
    client.loop_start()
    return client


def publish_value(client: "mqtt_client_mod.Client", topic: str, value: Any) -> None:
    """Publish a value, but only when it changed since last publish (retained)."""
    if value is None:
        return
    payload = str(value)
    if _PUBLISHED_CACHE.get(topic) == payload:
        return
    _PUBLISHED_CACHE[topic] = payload
    client.publish(topic, payload, retain=True)


# ---------------------------------------------------------------------------
# Mower state → flat datapoints
# ---------------------------------------------------------------------------
def _coalesce(obj: Any, *path: str) -> Any:
    """Walk a getattr/getitem path defensively. Returns None on any failure."""
    cur = obj
    for p in path:
        if cur is None:
            return None
        try:
            cur = getattr(cur, p)
        except AttributeError:
            try:
                cur = cur[p]
            except Exception:  # noqa: BLE001
                return None
    return cur


def extract_datapoints(device: Any) -> dict[str, Any]:
    """Pull a flat dict of MQTT-publishable values from a pymammotion MowerDevice.

    Keys mirror the Mammotion-HA sensor names so docs map cleanly. Missing
    fields are silently dropped (some are model-dependent — Luba has
    blade_height, Yuka has vision_state, RTK base stations have neither).
    """
    out: dict[str, Any] = {}
    rd = _coalesce(device, "report_data")

    # Battery and basic device state
    battery = _coalesce(rd, "dev", "battery_val")
    if battery is not None:
        out["battery_percent"] = int(battery)
    sys_status = _coalesce(rd, "dev", "sys_status")
    if sys_status is not None:
        out["activity_mode"] = int(sys_status)
    charge_state = _coalesce(rd, "dev", "charge_state")
    if charge_state is not None:
        out["charge_state"] = int(charge_state)

    # Signal strength
    for attr, key in (
        ("ble_rssi", "ble_rssi"),
        ("wifi_rssi", "wifi_rssi"),
        ("mnet_rssi", "mnet_rssi"),
    ):
        v = _coalesce(rd, "connect", attr)
        if v is not None:
            out[key] = int(v)
    connect_type = _coalesce(rd, "connect", "connect_type")
    if connect_type is not None:
        out["connect_type"] = int(connect_type)

    # RTK / GPS
    gps_stars = _coalesce(rd, "rtk", "gps_stars")
    if gps_stars is not None:
        out["gps_stars"] = int(gps_stars)
    rtk_status = _coalesce(rd, "rtk", "status")
    if rtk_status is not None:
        out["positioning_mode"] = int(rtk_status)
    co_view = _coalesce(rd, "rtk", "co_view_stars")
    if co_view is not None:
        out["l1_satellites"] = int(co_view) & 0xFF
        out["l2_satellites"] = (int(co_view) >> 8) & 0xFF

    # Work progress (HA mirrors the bit-packing scheme)
    area_field = _coalesce(rd, "work", "area")
    if area_field is not None:
        out["area_m2"] = int(area_field) & 0xFFFF
        out["progress_percent"] = int(area_field) >> 16
    progress_field = _coalesce(rd, "work", "progress")
    if progress_field is not None:
        out["total_time_min"] = int(progress_field) & 0xFFFF
        out["left_time_min"] = int(progress_field) >> 16
    man_speed = _coalesce(rd, "work", "man_run_speed")
    if man_speed is not None:
        out["mowing_speed_mps"] = round(int(man_speed) / 100.0, 3)
    knife_height = _coalesce(rd, "work", "knife_height")
    if knife_height is not None:
        out["blade_height_mm"] = int(knife_height)

    # Maintenance
    mileage = _coalesce(rd, "maintenance", "mileage")
    if mileage is not None:
        out["maintenance_distance_m"] = int(mileage)
    work_time = _coalesce(rd, "maintenance", "work_time")
    if work_time is not None:
        out["maintenance_work_time_s"] = int(work_time)
    bat_cycles = _coalesce(rd, "maintenance", "bat_cycles")
    if bat_cycles is not None:
        out["maintenance_bat_cycles"] = int(bat_cycles)
    blade_used = _coalesce(rd, "maintenance", "blade_used_time", "blade_used_time")
    if blade_used is not None:
        out["blade_used_time_s"] = int(blade_used)
    blade_warn = _coalesce(rd, "maintenance", "blade_used_time", "blade_used_warn_time")
    if blade_warn is not None:
        out["blade_used_warn_time_s"] = int(blade_warn)

    # Vision (Luba 2 / Yuka)
    brightness = _coalesce(rd, "vision_info", "brightness")
    if brightness is not None:
        out["camera_brightness"] = int(brightness)
    vio_state = _coalesce(rd, "vision_info", "vio_state")
    if vio_state is not None:
        out["visual_positioning_status"] = int(vio_state)

    # Location (in radians on the wire → convert to degrees for Loxone)
    import math
    lat = _coalesce(device, "location", "RTK", "latitude")
    lon = _coalesce(device, "location", "RTK", "longitude")
    if isinstance(lat, (int, float)):
        out["rtk_latitude_deg"] = round(float(lat) * 180.0 / math.pi, 7)
    if isinstance(lon, (int, float)):
        out["rtk_longitude_deg"] = round(float(lon) * 180.0 / math.pi, 7)
    pos_type = _coalesce(device, "location", "position_type")
    if pos_type is not None:
        out["position_type"] = int(pos_type)

    # Firmware / serial / online flag
    sn = _coalesce(device, "device_firmwares", "device_version") or _coalesce(device, "name")
    if sn is not None:
        out["device_name"] = str(sn)
    online = _coalesce(device, "online")
    if online is not None:
        out["online"] = 1 if online else 0

    return out


# ---------------------------------------------------------------------------
# Command bridge (MQTT → pymammotion)
# ---------------------------------------------------------------------------
# Map of MQTT command names → (pymammotion command, kwargs adapter)
# Each adapter takes the raw payload string and returns kwargs for
# MammotionClient.send_command_with_args(device_name, cmd, **kwargs).
COMMAND_MAP: dict[str, tuple[str, Any]] = {
    "start_task":          ("start_job",            lambda _p: {}),
    "pause":               ("pause_execute_task",   lambda _p: {}),
    "resume":              ("resume_execute_task",  lambda _p: {}),
    "cancel":              ("cancel_job",           lambda _p: {}),
    "return_to_dock":      ("return_to_dock",       lambda _p: {}),
    "leave_dock":          ("leave_dock",           lambda _p: {}),
    "move_forward":        ("move_forward",         lambda _p: {}),
    "move_back":           ("move_back",            lambda _p: {}),
    "move_left":           ("move_left",            lambda _p: {}),
    "move_right":          ("move_right",           lambda _p: {}),
    "sync_maps":           ("sync_maps",            lambda _p: {}),
    "restart_mower":       ("reboot_system",        lambda _p: {}),
}


# ---------------------------------------------------------------------------
# Auth-error classification
# ---------------------------------------------------------------------------
# Mammotion returns a small set of "the user supplied bad credentials"
# messages. Retrying these is counterproductive: every retry costs another
# failed-login charge against the account's anti-bruteforce budget, and once
# the threshold is crossed Mammotion locks the account for 5+ minutes and the
# next retry pushes the cooldown out again. The daemon should detect these
# and stop immediately with a clear log line, letting the user fix the
# credentials in the LoxBerry UI before any further attempt.
_PERMANENT_AUTH_PATTERNS = (
    "account or password mismatch",
    "password mismatch",
    "user not exist",
    "user does not exist",
    "account does not exist",
    "too many password verification errors",
    "account is locked",
    "captcha required",
    "invalid credentials",
    "invalid email",
    "email format",
    "wrong password",
)


def is_permanent_auth_failure(error: BaseException) -> bool:
    msg = str(error).lower()
    return any(pat in msg for pat in _PERMANENT_AUTH_PATTERNS)


async def handle_command(
    client_pm: Any,
    device_name: str,
    command_name: str,
    payload: str,
) -> None:
    spec = COMMAND_MAP.get(command_name)
    if spec is None:
        log.warning("Ignoring unknown command %r for %s", command_name, device_name)
        return
    pm_cmd, adapter = spec
    try:
        kwargs = adapter(payload) or {}
    except Exception as e:  # noqa: BLE001
        log.error("Bad payload for command %s: %s", command_name, e)
        return
    log.info("Dispatch %s -> %s(%s) kwargs=%s", device_name, command_name, pm_cmd, kwargs)
    try:
        await client_pm.send_command_with_args(device_name, pm_cmd, **kwargs)
    except Exception as e:  # noqa: BLE001
        log.error("Command %s failed for %s: %s", command_name, device_name, e)


# ---------------------------------------------------------------------------
# Main async loop
# ---------------------------------------------------------------------------
async def amain() -> int:
    cfg = load_config()

    if not cfg["enabled"]:
        log.info("Plugin is disabled — soft-exit. Enable it in the LoxBerry web UI to start.")
        return 0

    if not (cfg["account_email"] and cfg["account_password"]):
        log.warning(
            "Mammotion account credentials not configured — soft-exit. "
            "Set them in the LoxBerry web UI."
        )
        return 0

    try:
        from pymammotion.client import MammotionClient
    except ImportError as e:
        log.error(
            "pymammotion is not installed: %s. Install it manually with: "
            "/opt/loxberry/data/plugins/%s/venv/bin/pip install pymammotion",
            e,
            PLUGIN_NAME,
        )
        return 0

    prefix = cfg["mqtt_topic_prefix"]

    if cfg["register_mqtt_subscription"]:
        try:
            register_mqtt_subscription(prefix)
        except Exception as e:  # noqa: BLE001
            log.warning("Failed to register MQTT subscription file: %s", e)

    mqtt_client = build_mqtt_client(cfg)

    # Mammotion's server checks the App-Version header and rejects anything
    # that doesn't look like an official Mammotion-HA build. PyMammotion
    # formats this as f"HA,2.{ha_version}", so passing "0.5.47" produces
    # "HA,2.0.5.47" — the fingerprint Mammotion-HA users currently send.
    # See PyMammotion #137 / Mammotion-HA #750: any other prefix returns
    # the misleading 'Account or password mismatch' even with valid creds.
    ha_version = (cfg.get("ha_version_tag") or "").strip() or "0.5.47"
    log.info("PyMammotion App-Version fingerprint: HA,2.%s", ha_version)
    pm = MammotionClient(ha_version=ha_version)
    loop = asyncio.get_running_loop()

    stop_event = asyncio.Event()

    # PyMammotion's TokenManager runs its own background refresh. When all
    # recovery attempts (relogin, token refresh, reconnect) are exhausted, it
    # fires on_unrecoverable_auth_error. The HA integration treats this as a
    # signal to start a re-auth flow; here we have no interactive way to
    # re-prompt for credentials, so we publish auth_failed and ask the loop
    # to stop cleanly. The user fixes credentials in the UI → daemon restart
    # picks them up. Without this wire-up the failure happens inside an
    # asyncio Task we never await, so it would silently terminate the loop
    # and leave a stale pidfile (which is the exact bug 0.1.0 had).
    async def _on_unrecoverable_auth(exc: BaseException) -> None:
        log.error(
            "PyMammotion reports unrecoverable auth error: %s. "
            "Stopping cleanly. Re-enter credentials and click 'Save and restart daemon'.",
            str(exc)[:240],
        )
        mqtt_client.publish(f"{prefix}/_status", "auth_failed", retain=True)
        mqtt_client.publish(f"{prefix}/_last_error", str(exc)[:240], retain=True)
        stop_event.set()

    pm.on_unrecoverable_auth_error = _on_unrecoverable_auth

    def _signal_stop(signum, _frame=None):
        log.info("Received signal %s — shutting down", signum)
        loop.call_soon_threadsafe(stop_event.set)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_stop, sig)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, _signal_stop)

    # ----- login + device discovery, with bounded retries -----
    # Auth errors do NOT retry — repeated wrong-password attempts will
    # escalate Mammotion's anti-bruteforce lockout and lock out the account.
    # Network/server errors retry with bounded exponential backoff.
    delay = 5.0
    login_ok = False
    for attempt in range(1, 7):
        try:
            log.info("Logging in to Mammotion cloud as %s (attempt %d)", cfg["account_email"], attempt)
            await pm.login_and_initiate_cloud(cfg["account_email"], cfg["account_password"])
            login_ok = True
            break
        except Exception as e:  # noqa: BLE001
            msg = str(e).replace(cfg["account_password"], "***")
            if is_permanent_auth_failure(e):
                log.error(
                    "Cloud login REJECTED (permanent): %s. "
                    "Soft-exit — fix the credentials in the LoxBerry UI and click 'Save and restart daemon'. "
                    "Further automatic retries are disabled to avoid an account lockout.",
                    msg,
                )
                mqtt_client.publish(f"{prefix}/_status", "auth_failed", retain=True)
                mqtt_client.publish(f"{prefix}/_last_error", msg[:240], retain=True)
                mqtt_client.loop_stop()
                mqtt_client.disconnect()
                return 0  # soft-exit so wrapper sees a clean stop, not a crash loop
            log.error("Cloud login failed (transient): %s", msg)
            if attempt == 6 or stop_event.is_set():
                mqtt_client.publish(f"{prefix}/_status", "error", retain=True)
                mqtt_client.publish(f"{prefix}/_last_error", msg[:240], retain=True)
                mqtt_client.loop_stop()
                mqtt_client.disconnect()
                return 0  # soft-exit on exhausted transient retries — pidfile cleaned by atexit
            delay = min(delay * 1.7, 300.0)
            log.info("Retrying login in %.1fs", delay)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                return 0
            except asyncio.TimeoutError:
                pass

    if not login_ok:
        # Should be unreachable — every branch above handles its own exit.
        return 0

    mqtt_client.publish(f"{prefix}/_last_error", "", retain=True)

    devices = list(pm.device_registry.all_devices)
    log.info("Cloud login OK — %d device(s) registered", len(devices))
    mqtt_client.publish(f"{prefix}/_device_count", str(len(devices)), retain=True)

    if not devices:
        log.warning(
            "No mowers found on this Mammotion account. Make sure the mower is shared "
            "to this account in the Mammotion phone app."
        )

    # ----- start continuous report streams so state is fresh -----
    device_names: list[str] = []
    for handle in devices:
        name = getattr(handle, "device_name", None) or getattr(handle, "name", None)
        if not name:
            continue
        device_names.append(name)
        try:
            pm.setup_device_watchers(name)
            await pm.request_iot_sync_continuous(name, period=1000, no_change_period=4000)
            log.info("Streaming reports started for %s", name)
        except Exception as e:  # noqa: BLE001
            log.warning("Could not start report stream for %s: %s", name, e)

    # ----- command bridge: subscribe per device -----
    if cfg["enable_commands"]:
        suffix = cfg.get("command_topic_suffix", "set") or "set"

        def _on_command_message(_c, _userdata, msg):
            try:
                payload = msg.payload.decode("utf-8", errors="replace").strip()
            except Exception:  # noqa: BLE001
                payload = ""
            log.debug("Command MQTT in: %s payload=%r", msg.topic, payload)
            # Topic shape: <prefix>/<device>/<suffix>/<command>
            parts = msg.topic.split("/")
            if len(parts) < 4:
                return
            device_segment, suffix_segment, command_name = parts[-3], parts[-2], parts[-1]
            if suffix_segment != suffix:
                return
            target = None
            for n in device_names:
                if safe_segment(n) == device_segment:
                    target = n
                    break
            if target is None:
                log.warning("Command for unknown device %r ignored", device_segment)
                return
            asyncio.run_coroutine_threadsafe(
                handle_command(pm, target, command_name, payload), loop
            )

        mqtt_client.on_message = _on_command_message
        for name in device_names:
            seg = safe_segment(name)
            sub_topic = f"{prefix}/{seg}/{suffix}/+"
            mqtt_client.subscribe(sub_topic, qos=0)
            log.info("Subscribed to command topic %s", sub_topic)

    # ----- publish health -----
    mqtt_client.publish(f"{prefix}/_status", "online", retain=True)

    # ----- main polling loop -----
    poll_interval = cfg["poll_interval_seconds"]
    log.info("Entering poll loop (interval=%ds, prefix=%s)", poll_interval, prefix)

    while not stop_event.is_set():
        cycle_start = time.monotonic()
        try:
            for handle in pm.device_registry.all_devices:
                name = getattr(handle, "device_name", None) or getattr(handle, "name", None)
                if not name:
                    continue
                device_seg = safe_segment(name)
                snapshot = getattr(handle, "snapshot", None)
                raw = getattr(snapshot, "raw", None) if snapshot else None
                if raw is None:
                    continue
                points = extract_datapoints(raw)
                for key, value in points.items():
                    publish_value(mqtt_client, f"{prefix}/{device_seg}/{key}", value)
                mqtt_client.publish(
                    f"{prefix}/{device_seg}/_last_update_epoch",
                    str(int(time.time())),
                    retain=True,
                )
            mqtt_client.publish(f"{prefix}/_last_poll_epoch", str(int(time.time())), retain=True)
        except Exception as e:  # noqa: BLE001
            log.exception("Poll cycle failed: %s", e)
            mqtt_client.publish(f"{prefix}/_status", "error", retain=True)

        elapsed = time.monotonic() - cycle_start
        sleep_for = max(1.0, poll_interval - elapsed)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=sleep_for)
        except asyncio.TimeoutError:
            pass

    # ----- shutdown -----
    log.info("Stopping MammotionClient")
    try:
        await asyncio.wait_for(pm.stop(), timeout=10.0)
    except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
        log.warning("Error during MammotionClient shutdown: %s", e)
    mqtt_client.publish(f"{prefix}/_status", "offline", retain=True)
    time.sleep(0.5)
    mqtt_client.loop_stop()
    mqtt_client.disconnect()
    log.info("Daemon exited cleanly")
    return 0


def main() -> int:
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        return 0
    except Exception as e:  # noqa: BLE001
        log.exception("Fatal error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
