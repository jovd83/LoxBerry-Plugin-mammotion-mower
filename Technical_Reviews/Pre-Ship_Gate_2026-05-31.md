# Pre-Ship Security Gate — Mammotion Mower v1.0.1

- Date: 2026-05-31
- Repo: `jovd83/LoxBerry-Plugin-mammotion-mower` (PUBLIC — visibility unchanged)
- Release target: **v1.0.1** (patch over the in-progress 1.0.0)
- Gate policy: all three checks must run and produce an artifact before any GitHub push/release.
- Compliance note: checks 1 and 2 were **satisfied directly (agent-performed equivalent)**, not via a separate launch of the named skills; check 3 used the real `pip-audit` tool. Recorded per the dedup/record rule.

## 1. modern-dependency-guard (dependency currency & stack risk) — ⚠️ FINDING

| Dependency | Pinned (`requirements.txt`) | PyPI latest | Assessment |
|---|---|---|---|
| `pymammotion` | `>=0.5.45` (no upper bound) | **0.7.131** | ⚠️ **Drift risk.** A fresh `pip install` resolves to 0.7.x, but the daemon was written against the 0.5.x API (`MammotionClient(ha_version=…)`, `login_and_initiate_cloud`, `device_registry.all_devices`, `setup_device_watchers`, `request_iot_sync_continuous`, `send_command_with_args`, `on_unrecoverable_auth_error`). No advisory, but a possible runtime/API-compat break on new installs. |
| `paho-mqtt` | `>=1.6` | 2.1.0 | ✅ OK. Code already supports both 1.x and 2.x (`CallbackAPIVersion` guard in `build_mqtt_client`). |

**Recommendation (non-blocking for 1.0.1):** add a tested upper bound to `pymammotion` (e.g. `>=0.5.45,<0.8`) or verify the daemon against 0.7.x before the next release. This risk is **pre-existing** (not introduced by 1.0.1), so it does not block this hardening patch, but it should be tracked.

## 2. api-contract-sentinel (API contract drift) — N/A (no contract surface)

No authoritative API contract exists in the repo (no OpenAPI / AsyncAPI / Protobuf). The plugin is an MQTT bridge; its de-facto contract is the MQTT topic surface (`mammotion/<device>/…` and `mammotion/_status|_last_error|_device_count|_last_poll_epoch`), which is documented in `README.md` and `docs/LOXONE_CONFIG.md`. Nothing for the sentinel to diff against. **No drift possible; gate item N/A.**

## 3. Dependency advisory scan — ✅ PASS

```
$ python -m pip_audit -r requirements.txt --vulnerability-service osv
No known vulnerabilities found
```
- Tool: `pip-audit` (PyPA), vulnerability service: OSV.
- Result: **no known vulnerabilities** in `pymammotion` / `paho-mqtt` (or their resolved transitive deps).

## Gate verdict

| Check | Status |
|---|---|
| modern-dependency-guard | ⚠️ Pass with finding (pymammotion unbounded pin — pre-existing, tracked) |
| api-contract-sentinel | N/A (no contract surface) |
| advisory scan (pip-audit/OSV) | ✅ Pass |

**Cleared to release v1.0.1.** Follow-up tracked: bound the `pymammotion` version range.
