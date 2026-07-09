#!/usr/bin/env python3
"""
Runtime smoke validation for dessmonitor.
Verifies that all safe Python modules can be imported without requiring
config files, device definitions, network access, or external services.
Does NOT start the application server.
"""

import importlib
import os
import sys

# Ensure the repository root is on sys.path so that 'app', 'shared_state',
# and 'service' packages are importable.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Modules known to be unsafe for smoke validation (import-time side effects)
UNSAFE_MODULES = {
    "app.tuya.tuya_authorisation",  # Config() and DeviceInitializer() at module level
}

# All other project modules to verify
MODULES_TO_CHECK = [
    "app.api",
    "app.config",
    "app.ml.timescale_data_collector",
    "app.ml.ml_data_analyzer",
    "app.ml.ml_data_collector",
    "app.tuya.relay_tuya_controller",
    "app.weather.openweather_service",
    "app.monitoring.device_status_logger",
    "app.device_initializer",
    "app.logger",
    "shared_state.shared_state",
    "service.inverter_monitor",
]


def safe_env() -> None:
    """Set safe environment defaults to prevent accidental initialization."""
    os.environ.setdefault("TS_ENABLED", "false")
    # Other env vars are left unset -- the code must handle missing values gracefully
    # MONITOR_CONFIG_JSON, MONITOR_CONFIG_PATH, etc. are deliberately not set


def main() -> int:
    safe_env()

    errors: list[str] = []

    for mod_name in MODULES_TO_CHECK:
        try:
            importlib.import_module(mod_name)
            print(f"  ✅ {mod_name}")
        except Exception as exc:
            errors.append(f"  ❌ {mod_name}: {exc}")
            print(f"  ❌ {mod_name}: {exc}")

    # Report unsafe modules (informational only -- not checked)
    for mod_name in sorted(UNSAFE_MODULES):
        print(f"  ⏭️  {mod_name} (skipped -- import-time side effects)")

    print()
    if errors:
        print(f"❌ {len(errors)} module(s) failed to import.")
        return 1

    print(f"✅ All {len(MODULES_TO_CHECK)} module(s) imported successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
