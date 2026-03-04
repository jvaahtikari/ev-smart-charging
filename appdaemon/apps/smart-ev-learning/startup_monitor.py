"""
startup_monitor.py — EV AppDaemon startup health monitor.
Waits for all EV scripts to initialise after AppDaemon starts, then sets
sensor.ev_system_ready to 'true'. The UI shows 'System starting...' until this
entity becomes 'true'.

Writes via input_text.ev_system_ready so the pass-through template sensor
sensor.ev_system_ready picks it up without HA/AppDaemon entity conflicts.
"""

import appdaemon.plugins.hass.hassapi as hass

from utils import ha_write

# input_text backend that the template sensor reads from
BACKEND_ENTITY = "input_text.ev_system_ready"

# How long to wait before checking (seconds) — gives other scripts time to register
STARTUP_DELAY_SEC = 10

# All EV AppDaemon app names that must be loaded before declaring ready
REQUIRED_APPS = [
    "ev_mode_resolver",
    "ev_status_narrator",
    "ev_chart_data_writer",
    "ev_predictor",
    "ev_trip_logger",
    "ev_model_updater",
]


class EVStartupMonitor(hass.Hass):

    def initialize(self):
        self.log("EVStartupMonitor: initialising — will check readiness in "
                 f"{STARTUP_DELAY_SEC}s")
        # Mark not-ready immediately on (re)start
        ha_write(self, BACKEND_ENTITY, "false",
                 {"friendly_name": "EV System Ready"})
        self.run_in(self._check_ready, STARTUP_DELAY_SEC)

    def _check_ready(self, kwargs):
        """Check that all required apps are loaded; set ready or retry."""
        missing = []
        for app_name in REQUIRED_APPS:
            try:
                app = self.get_app(app_name)
                if app is None:
                    missing.append(app_name)
            except Exception:
                missing.append(app_name)

        if missing:
            self.log(
                f"EVStartupMonitor: not ready — missing apps: {missing}. "
                f"Retrying in {STARTUP_DELAY_SEC}s",
                level="WARNING",
            )
            self.run_in(self._check_ready, STARTUP_DELAY_SEC)
        else:
            ha_write(self, BACKEND_ENTITY, "true",
                     {"friendly_name": "EV System Ready"})
            self.log("EVStartupMonitor: all EV apps loaded — system ready.")
