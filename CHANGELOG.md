# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
### Changed
### Fixed
### Removed

## [1.0.1] - 2026-05-31

### Changed
- Centralized credential redaction: every error path (login retries,
  exhausted transient retries, and the unrecoverable-auth callback) now scrubs
  the account password out of log lines and the retained MQTT `_last_error`
  topic via a single `redact_secret()` helper.
- Web UI derives the daemon path from `$lbhomedir` instead of a hardcoded
  `/opt/loxberry/...` install root.

### Removed
- Dead `command_settle_seconds` config key (was declared and persisted but
  never read) and the unused `redact_url()` helper.

### Fixed
- Hoisted `import math` out of the per-cycle `extract_datapoints()` hot path.
- Added a debug log when the maintenance block is present but the blade-life
  counters resolve to `None`, to flag possible PyMammotion shape drift.

### Added
- `.github/workflows/validate.yml` CI gate (Python byte-compile, `bash -n`,
  JSON validity, `plugin.cfg` sanity, ruff) and a unit test for
  `extract_datapoints()` and `safe_segment()`.

## [1.0.0] - 2026-05-29

### Changed
- Plugin author set to `jovd83` in `plugin.cfg`; first stable release.

## [0.1.2] - 2026-05-28

### Fixed
- Resolved the misleading *"Account or password mismatch"* error that occurred
  even with valid credentials: Mammotion's server rejects any `App-Version`
  header that does not look like an official Mammotion-HA build. Added a
  configurable **App-Version fingerprint** (default `0.5.47`, sent as
  `App-Version: HA,2.<value>`). See PyMammotion #137 / Mammotion-HA #750.

## [0.1.1] - 2026-05-27

### Fixed
- Daemon now cleans its own pidfile on every exit path via `atexit`, so a
  self-terminating daemon no longer shows *"stopped (stale pidfile)"* forever.
- Permanent authentication failures (bad credentials, locked account, captcha)
  are detected and stop the daemon immediately instead of retrying, preventing
  Mammotion's anti-bruteforce lockout from being repeatedly re-triggered.

## [0.1.0] - 2026-05-22

### Added
- Initial release: bridges Mammotion robot mowers to the LoxBerry MQTT broker
  via PyMammotion, with live state streaming, per-device datapoint publishing,
  and an MQTT command bridge (start/pause/resume/cancel/dock/etc.).
- Zero-config MQTT broker auto-discovery from LoxBerry `general.json` plus
  automatic MQTT Gateway subscription registration (`mqtt_subscriptions.cfg`).
- `postinstall.sh` bootstraps a relocatable standalone CPython 3.13 venv when
  the system Python is older than 3.12 (required by PyMammotion).
- Plugin icons and a settings web UI with a live daemon-status pill and a
  *Last failure* banner sourced from the daemon log.
