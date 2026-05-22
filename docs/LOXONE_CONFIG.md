# Loxone Config setup for Mammotion Mower

This guide connects the Mammotion Mower plugin to a Loxone Miniserver via the
**built-in LoxBerry MQTT Gateway** — no extra plugin required on LoxBerry 3.x.

## 1. Make sure the daemon is running

1. Open *Plugins → Mammotion Mower* in the LoxBerry web UI.
2. The status pill at the top must read **running (PID …)** (green).
3. If not, see *Troubleshooting* in the README.

## 2. Confirm topics arrive on the LoxBerry broker

Open a terminal on the LoxBerry (SSH as `loxberry`):

```bash
mosquitto_sub -h localhost -t 'mammotion/#' -v
```

You should see one line per datapoint and per mower, e.g.:

```text
mammotion/_status online
mammotion/_device_count 1
mammotion/luba-xxxxxx/battery_percent 87
mammotion/luba-xxxxxx/activity_mode 0
mammotion/luba-xxxxxx/wifi_rssi -52
…
```

If nothing arrives, the daemon either hasn't logged in yet or the account
has no mowers. Check
`/opt/loxberry/log/plugins/mammotion-mower/mammotion-mower.log`.

## 3. Verify the MQTT Gateway picks them up

The plugin writes `mammotion/#` into
`/opt/loxberry/config/plugins/mammotion-mower/mqtt_subscriptions.cfg` on
every start. The LoxBerry built-in MQTT Gateway watches that file with
inotify and merges it into its subscription list automatically — no manual
step needed.

In the LoxBerry web UI:

1. Open *MQTT Gateway* (built-in, top navigation).
2. Click *Incoming Overview*.
3. You should see the `mammotion/...` topics listed with their last values.
4. Each row has a *Convert as Virtual Input* button — click it for every
   datapoint you want in Loxone.

## 4. Add Virtual Inputs in Loxone Config

For each topic you converted in step 3:

1. Open Loxone Config and connect to your Miniserver.
2. *Periphery tree → Virtual Inputs → Add Virtual Input*.
3. Use the **exact name** MQTT Gateway shows under *Incoming Overview*. Names
   are case-sensitive and copy/paste-friendly.

Suggested core inputs:

| MQTT topic | Suggested Virtual Input name | Type |
|---|---|---|
| `mammotion/luba-xxxxxx/battery_percent` | `mammotion_battery_percent` | Analog |
| `mammotion/luba-xxxxxx/activity_mode` | `mammotion_activity_mode` | Analog |
| `mammotion/luba-xxxxxx/progress_percent` | `mammotion_progress_percent` | Analog |
| `mammotion/luba-xxxxxx/left_time_min` | `mammotion_left_time_min` | Analog |
| `mammotion/luba-xxxxxx/wifi_rssi` | `mammotion_wifi_rssi` | Analog |
| `mammotion/luba-xxxxxx/online` | `mammotion_online` | Digital |
| `mammotion/_status` | `mammotion_plugin_status` | Text |

## 5. Sending commands from Loxone

The plugin subscribes to `mammotion/<device>/set/<command>`. Loxone needs a
**Virtual Output** that publishes to the local broker via MQTT Gateway's
*Outgoing Connections* feature.

Easiest path: use *MQTT Gateway → Outgoing Connections* to define a
named outbound command (e.g. *mammotion-luba-start*) bound to:

- Topic: `mammotion/luba-xxxxxx/set/start_task`
- Payload: `1` (any non-empty payload works — only the topic name is parsed)

Then in Loxone Config, create a *Virtual Output Command* that calls the
MQTT Gateway HTTP endpoint for that named command. The MQTT Gateway docs
have screenshots for this; the exact URL is shown next to each outgoing
command in its UI.

### Useful command bindings

| Loxone button | MQTT topic | Payload |
|---|---|---|
| Start mowing | `mammotion/<device>/set/start_task` | `1` |
| Pause | `mammotion/<device>/set/pause` | `1` |
| Resume | `mammotion/<device>/set/resume` | `1` |
| Cancel | `mammotion/<device>/set/cancel` | `1` |
| Return to dock | `mammotion/<device>/set/return_to_dock` | `1` |
| Sync maps | `mammotion/<device>/set/sync_maps` | `1` |

Commands that move the mower physically (`move_forward`, `move_back`,
`move_left`, `move_right`, `leave_dock`) are intended for diagnostic nudges
only. Don't hook them up to automations.

## 6. Verification checklist

- [ ] `mammotion/_status` reads `online` in MQTT Gateway.
- [ ] All chosen datapoints appear in MQTT Gateway *Incoming Overview*.
- [ ] Virtual Input names in Loxone Config match the MQTT Gateway names
      exactly (case-sensitive).
- [ ] The mower's *charge_state* changes within seconds of plugging it in
      (validates the live stream is open).
- [ ] A test `set/pause` from Loxone is visible in the daemon log within 1 s.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Topic visible in MQTT Gateway but not in Loxone | Virtual Input name mismatch | Copy the exact name from MQTT Gateway |
| No MQTT topics published | Daemon stopped, or cloud login failed | Check `mammotion-mower.log` |
| Values are stale | Cloud session lost — pymammotion will reconnect, see log | Restart daemon from the plugin page |
| Commands accepted but mower doesn't react | Mower offline / out of range / 4G dead | Confirm `mammotion/<device>/online` is `1` |
| Plugin page shows 500 | CGI not executable | Re-run `postinstall.sh` or `chmod 0755 /opt/loxberry/webfrontend/htmlauth/plugins/mammotion-mower/index.cgi` |
