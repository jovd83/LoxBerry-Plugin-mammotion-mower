"""Unit tests for the pure helpers in bin/mammotion-mower.py.

The daemon module has a hyphenated filename and does import-time setup
(directory creation + a rotating log handler), so it is loaded via importlib
with the LoxBerry dirs pointed at a throwaway temp directory.
"""

import importlib.util
import math
import os
import sys
import tempfile
import types
from pathlib import Path


def _stub_paho_if_missing():
    """The daemon imports ``paho.mqtt.client`` at module scope, but the helpers
    under test never touch it. Stub the package when it is not installed so the
    unit tests stay runtime-free; CI installs the real package."""
    try:
        import paho.mqtt.client  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    paho = types.ModuleType("paho")
    mqtt = types.ModuleType("paho.mqtt")
    client = types.ModuleType("paho.mqtt.client")
    client.Client = object
    mqtt.client = client
    paho.mqtt = mqtt
    sys.modules.update({"paho": paho, "paho.mqtt": mqtt, "paho.mqtt.client": client})


def _load_daemon():
    _stub_paho_if_missing()
    tmp = Path(tempfile.mkdtemp(prefix="mammotion-test-"))
    for var in ("LBPCONFIGDIR", "LBPLOGDIR", "LBPDATADIR"):
        os.environ[var] = str(tmp / var.lower())
    repo_root = Path(__file__).resolve().parent.parent
    module_path = repo_root / "bin" / "mammotion-mower.py"
    spec = importlib.util.spec_from_file_location("mammotion_daemon", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["mammotion_daemon"] = module
    spec.loader.exec_module(module)
    return module


daemon = _load_daemon()


class _Box:
    """Minimal attribute bag emulating PyMammotion's nested report structs."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_safe_segment_basic():
    assert daemon.safe_segment("Luba-AWD 5000") == "luba-awd_5000"
    assert daemon.safe_segment("") == "device"
    assert daemon.safe_segment(None) == "device"
    assert daemon.safe_segment("a/b#c+d") == "a_b_c_d"


def test_redact_secret():
    assert daemon.redact_secret("pw=hunter2", "hunter2") == "pw=***"
    # an empty secret must never blank the whole string
    assert daemon.redact_secret("abc", "") == "abc"
    assert daemon.redact_secret(Exception("boom secret"), "secret") == "boom ***"


def test_extract_datapoints_core():
    rd = _Box(
        dev=_Box(battery_val=87, sys_status=13, charge_state=1),
        connect=_Box(ble_rssi=-40, wifi_rssi=-52, mnet_rssi=None, connect_type=2),
        rtk=_Box(gps_stars=12, status=4, co_view_stars=(3 << 8) | 5),
        work=_Box(
            area=(50 << 16) | 1200,
            progress=(30 << 16) | 90,
            man_run_speed=45,
            knife_height=30,
        ),
        maintenance=_Box(
            mileage=1000,
            work_time=3600,
            bat_cycles=12,
            blade_used_time=_Box(blade_used_time=7200, blade_used_warn_time=10000),
        ),
        vision_info=_Box(brightness=128, vio_state=1),
    )
    device = _Box(
        report_data=rd,
        location=_Box(RTK=_Box(latitude=0.5, longitude=0.1), position_type=3),
        device_firmwares=_Box(device_version="luba-123456"),
        name="luba",
        online=True,
    )

    out = daemon.extract_datapoints(device)

    assert out["battery_percent"] == 87
    assert out["activity_mode"] == 13
    assert out["charge_state"] == 1
    assert out["wifi_rssi"] == -52
    assert "mnet_rssi" not in out  # None is dropped
    assert out["connect_type"] == 2
    assert out["l1_satellites"] == 5
    assert out["l2_satellites"] == 3
    assert out["area_m2"] == 1200
    assert out["progress_percent"] == 50
    assert out["total_time_min"] == 90
    assert out["left_time_min"] == 30
    assert out["blade_height_mm"] == 30
    assert out["blade_used_time_s"] == 7200
    assert out["blade_used_warn_time_s"] == 10000
    assert out["device_name"] == "luba-123456"
    assert out["online"] == 1
    assert round(out["rtk_latitude_deg"], 3) == round(0.5 * 180.0 / math.pi, 3)


def test_extract_datapoints_handles_missing():
    out = daemon.extract_datapoints(_Box(report_data=None))
    assert isinstance(out, dict)
