# Mammotion Mower — LoxBerry plugin

Bring your **Mammotion robot mower** (Luba, Luba 2, Yuka and similar) into a
Loxone Miniserver. The plugin logs into the Mammotion cloud with PyMammotion,
keeps a live state stream open, and publishes every datapoint to the
LoxBerry MQTT broker. From the other direction it accepts commands on a set
of MQTT topics so Loxone can start / pause / cancel jobs and send the mower
back to its dock.

![LoxBerry](https://img.shields.io/badge/LoxBerry-3.0%2B-green)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)
![Version](https://img.shields.io/badge/version-0.1.0-orange)

## What it does

- **Monitoring** — battery, work progress, blade height, RSSI, RTK satellites,
  position, maintenance counters, error code, charge state and more, all
  published as retained MQTT topics that the LoxBerry MQTT Gateway picks up
  automatically and surfaces in the Miniserver as Virtual Inputs.
- **Control** — Loxone can publish to `mammotion/<device>/set/<command>` to
  start a task, pause/resume, cancel, return to dock, leave dock, sync maps,
  reboot, and emergency-nudge the mower.
- **Zero-config MQTT** — uses the built-in LoxBerry MQTT broker (auto-
  discovered from `general.json`) and auto-registers the topic prefix with the
  built-in MQTT Gateway. No manual subscription step.

The plugin uses [PyMammotion](https://github.com/mikey0000/PyMammotion) — the
same library that powers the
[Mammotion Home Assistant integration](https://github.com/mikey0000/Mammotion-HA).
PyMammotion is unofficial; Mammotion's Terms of Service prohibit unofficial
API access, so use at your own risk.

## Requirements

- LoxBerry 3.0 or newer.
- A Mammotion account with at least one mower bound to it.
- **Recommended:** create a *secondary* Mammotion account and share the mower
  to it. The Mammotion phone app and the daemon cannot stay logged in at the
  same time on the same account — they will kick each other out.
- Outbound internet access from LoxBerry to:
  - `*.mammotion.com` and the Aliyun IoT MQTT endpoint (for the mower link)
  - `github.com` (for the one-time Python 3.13 download — see below)
- **Python 3.13** at install time. LoxBerry 3.x ships Python **3.11**, which
  is too old for PyMammotion (it uses Python 3.12+ f-string syntax — PEP 701).
  The plugin's `postinstall.sh` detects this and **downloads a standalone
  Python 3.13** (≈30 MB) from
  [astral-sh/python-build-standalone](https://github.com/astral-sh/python-build-standalone)
  into `/opt/loxberry/data/plugins/mammotion-mower/python-standalone/`. No
  system changes; the standalone interpreter is used only by this plugin's
  venv. Supported architectures: `x86_64-linux` and `aarch64-linux`
  (Raspberry Pi 4/5 with the **64-bit** Pi OS). 32-bit Pi OS (`armv7l`) is
  **not supported**.

## Installation

1. Download `mammotion-mower-0.1.0.zip` from
   [GitHub Releases](https://github.com/jovd83/LoxBerry-Plugin-mammotion-mower/releases).
2. Open the LoxBerry web UI → *Plugin Install*.
3. Upload the ZIP and confirm with the SecurePIN.
4. Wait for the install to finish (allow **3–6 minutes the first time**) —
   the `postinstall.sh` script:
   - Downloads a standalone Python 3.13 build (~30 MB) into
     `/opt/loxberry/data/plugins/mammotion-mower/python-standalone/`,
     because LoxBerry's system Python (3.11) is too old.
   - Creates a venv at `/opt/loxberry/data/plugins/mammotion-mower/venv`.
   - `pip install pymammotion paho-mqtt` into it (~50 MB of deps).
5. Open *Plugins → Mammotion Mower*, tick **Enabled**, enter your account
   email + password, click **Save and restart daemon**.

The daemon will soft-exit with a clear log line if either *Enabled* is off or
credentials are missing, so the plugin never logs noisy errors during install.

## Configuration

| Setting | Description | Default |
|---|---|---|
| Enabled | Master switch — daemon soft-exits when off | off |
| Account email | Mammotion login (secondary/shared account recommended) | — |
| Account password | Stored plaintext in `default.json` mode 0600 | — |
| Poll interval | Floor between MQTT snapshot publishes (state arrives live regardless) | 60 s |
| Use built-in MQTT | Read broker host/port/user/pass from LoxBerry `general.json` | on |
| Topic prefix | Root for all MQTT topics | `mammotion` |
| Auto-register Gateway sub | Write `mqtt_subscriptions.cfg` so Gateway picks up `<prefix>/#` | on |
| Enable commands | Subscribe to `<prefix>/<device>/set/<command>` | on |
| Command topic suffix | Middle segment of command topics | `set` |
| Debug | DEBUG-level daemon logging | off |

## MQTT topics published

For each mower the plugin publishes to `mammotion/<device>/...`. The
`<device>` segment is derived from the mower's name (e.g. `luba-xxxxxx`) with
non-`[A-Za-z0-9_.-]` characters mapped to `_`.

| Topic | Unit | Notes |
|---|---|---|
| `mammotion/_status` | `online` / `offline` / `error` | Plugin-wide LWT |
| `mammotion/_device_count` | int | Number of mowers on the account |
| `mammotion/_last_poll_epoch` | unix s | Last successful poll cycle |
| `mammotion/<device>/battery_percent` | % | Main battery SOC |
| `mammotion/<device>/charge_state` | enum | 0 = idle, 1 = charging, … |
| `mammotion/<device>/activity_mode` | enum | sys_status; 0 = standby, 13 = mowing, 16 = updating, … |
| `mammotion/<device>/ble_rssi` | dBm | |
| `mammotion/<device>/wifi_rssi` | dBm | |
| `mammotion/<device>/mnet_rssi` | dBm | 4G/5G modem (Yuka mini) |
| `mammotion/<device>/connect_type` | enum | 0 = none, 1 = BLE, 2 = WiFi, 3 = 4G |
| `mammotion/<device>/gps_stars` | int | GPS satellites in view |
| `mammotion/<device>/l1_satellites` | int | RTK L1 satellites |
| `mammotion/<device>/l2_satellites` | int | RTK L2 satellites |
| `mammotion/<device>/positioning_mode` | enum | RTK fix status |
| `mammotion/<device>/area_m2` | m² | Area of the current task |
| `mammotion/<device>/progress_percent` | % | Progress through current task |
| `mammotion/<device>/total_time_min` | minutes | Total time of current task |
| `mammotion/<device>/left_time_min` | minutes | Remaining time |
| `mammotion/<device>/mowing_speed_mps` | m/s | Current ground speed |
| `mammotion/<device>/blade_height_mm` | mm | Cutting height (Luba only) |
| `mammotion/<device>/maintenance_distance_m` | m | Since last blade change |
| `mammotion/<device>/maintenance_work_time_s` | s | Since last service |
| `mammotion/<device>/maintenance_bat_cycles` | int | Battery cycles |
| `mammotion/<device>/blade_used_time_s` | s | Time on current blades |
| `mammotion/<device>/blade_used_warn_time_s` | s | Warn-threshold |
| `mammotion/<device>/camera_brightness` | enum | Luba 2 / Yuka |
| `mammotion/<device>/visual_positioning_status` | enum | VIO state |
| `mammotion/<device>/rtk_latitude_deg` | ° | Live mower position |
| `mammotion/<device>/rtk_longitude_deg` | ° | Live mower position |
| `mammotion/<device>/position_type` | enum | GPS / RTK / VIO fused |
| `mammotion/<device>/device_name` | string | Device serial / name |
| `mammotion/<device>/online` | 0 / 1 | Cloud-reported online flag |
| `mammotion/<device>/_last_update_epoch` | unix s | When this device was last refreshed |

## Commands accepted

Publish any non-empty payload (the command name is in the topic, not in the
body) to:

```text
mammotion/<device>/set/<command>
```

| Command | What it does | Notes |
|---|---|---|
| `start_task` | Start the active job | Requires a plan to be set on the device |
| `pause` | Pause the running job | |
| `resume` | Resume a paused job | |
| `cancel` | Cancel the running job | |
| `return_to_dock` | Send mower back to the charging dock | |
| `leave_dock` | Manual undock | |
| `sync_maps` | Re-fetch map / plan data from the device | |
| `restart_mower` | Reboot the mower (use sparingly) | |
| `move_forward` | Emergency nudge forward | Small jog only |
| `move_back` | Emergency nudge back | |
| `move_left` | Emergency nudge left | |
| `move_right` | Emergency nudge right | |

Unknown commands are logged and dropped — they never reach the cloud.

## Loxone Config

See [docs/LOXONE_CONFIG.md](docs/LOXONE_CONFIG.md) for step-by-step Virtual
Input and Virtual Output mapping examples.

## Logs

| File | What |
|---|---|
| `/opt/loxberry/log/plugins/mammotion-mower/mammotion-mower.log` | Daemon log (rotating, 2 MB × 3) |
| `/opt/loxberry/log/plugins/mammotion-mower/daemon-stderr.log` | Anything Python wrote to stderr |
| `/opt/loxberry/log/plugins/mammotion-mower/daemon-restart.log` | CGI restart hook output |

## Troubleshooting

| Problem | Check |
|---|---|
| Daemon shows *not configured* | Tick *Enabled* and enter both email + password |
| Daemon shows *stopped (stale pidfile)* | Check `daemon-stderr.log` — usually missing pymammotion or login rejected |
| `pymammotion is not installed` in the log | Re-run `postinstall.sh` or `su loxberry -c '/opt/loxberry/data/plugins/mammotion-mower/venv/bin/pip install pymammotion'` |
| `SyntaxError: f-string: unmatched '('` in stderr | Plugin venv is using Python 3.11 instead of the bundled standalone 3.13. Run `rm -rf /opt/loxberry/data/plugins/mammotion-mower/venv /opt/loxberry/data/plugins/mammotion-mower/python-standalone` then `bash /opt/loxberry/data/system/install/mammotion-mower/postinstall.sh` to rebuild. |
| Postinstall download fails behind a proxy | Set `https_proxy` in `/etc/environment` before installing, or manually drop a python-build-standalone tarball into `/opt/loxberry/data/plugins/mammotion-mower/python-standalone/` and extract |
| Cloud login fails immediately | Wrong email/password, or the same account is logged into the phone app. Switch to a shared secondary account. |
| MQTT topics not visible in MQTT Gateway | Confirm *Auto-register* is on. Otherwise add `mammotion/#` manually in MQTT Gateway → *Subscriptions*. |
| No values in Loxone | MQTT Gateway → *Incoming Overview* must show the topics. Then convert each to a Virtual Input with the exact name shown. |
| Battery looks stuck | The poll loop falls back to 60 s — live state pushes are independent. Check `_last_update_epoch` per device. |

## Security notes

- Account credentials are stored in `config/default.json` with mode `0600`.
  Anyone with `loxberry` shell access on the LoxBerry can read them.
- Use a *secondary*, mower-shared Mammotion account so a leaked credential
  cannot also unlock the phone app.
- The plugin does not open any inbound TCP/UDP port. All cloud traffic is
  outbound to Mammotion / Aliyun.

## Disclaimer

This is an **unofficial** integration that uses a reverse-engineered API.
Mammotion's Terms of Service prohibit unofficial API access. The maintainers
of this plugin, of PyMammotion, and of the Mammotion HA integration are not
affiliated with Mammotion. Use at your own risk; Mammotion may break the API
at any time.

## License

Apache License 2.0. See [LICENSE](LICENSE).

PyMammotion itself is also Apache 2.0 — see
<https://github.com/mikey0000/PyMammotion>.
