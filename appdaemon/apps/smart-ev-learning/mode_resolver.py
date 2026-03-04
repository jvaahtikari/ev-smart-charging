"""
mode_resolver.py — EV Mode Resolver
Watches all mode-relevant entities and writes sensor.ev_active_mode via
input_text.ev_active_mode (AppDaemon → HA conflict-free pattern).

Priority order (DESIGN_SPEC Section 2.2):
  P1  warning          — forecast reliability warning or system fault
  P2  not_plugged_crit — car home, unplugged, SOC low, window < 30 min
  P2  not_plugged      — car home, unplugged, SOC below daily floor
  P3  charge_now       — manual override active
  P4  journey_active   — journey enabled and car away
  P5  min_charge       — min-charge floor running
  P6  smart_charging   — planned slot currently active
  P7a smart_journey    — journey mode on, car home, plugged in, charging toward 100%
  P7b smart_bargain    — bargain fill active (spot below threshold)
  P7c smart_sufficient — SOC already covers predicted daily need
  P7d smart_waiting    — waiting for next cheap slot (default smart sub-state)
  P8  disabled         — system off
"""

from datetime import datetime, timezone

import appdaemon.plugins.hass.hassapi as hass

from utils import ha_write

# Backend entity — template sensor sensor.ev_active_mode reads from this
BACKEND_ENTITY = "input_text.ev_active_mode"

# All entities watched for mode changes
WATCHED_ENTITIES = [
    "input_boolean.ev_smart_charge_enabled",
    "input_boolean.ev_charge_now_override",
    "input_boolean.ev_min_charge_enabled",
    "binary_sensor.ev_should_charge_now_combined",
    "binary_sensor.ev_forecast_reliability_warning",
    "input_boolean.ev_journey_mode_enabled",
    "binary_sensor.ev_car_home",
    "sensor.smart_battery",
    "input_number.ev_min_soc_pct",
    "sensor.zag063912_charger_mode",
    "sensor.ev_learning_forecast_soc",
    "input_number.ev_bargain_price_threshold",
    "input_datetime.ev_deadline",
]

# Charger modes that mean "not plugged in"
NOT_PLUGGED_MODES = {"disconnected", "unknown"}

# How long a critically-short window is (minutes)
CRIT_WINDOW_MIN = 30

# Debounce delay — avoid rapid toggling on burst state changes
DEBOUNCE_SEC = 2


class EVModeResolver(hass.Hass):

    def initialize(self):
        self.log("EVModeResolver: initialising")
        self._debounce_handle = None

        for entity in WATCHED_ENTITIES:
            self.listen_state(self._on_state_change, entity)

        # Resolve once immediately so the entity has a value on startup
        self._resolve_mode({})
        self.log("EVModeResolver: watching all mode entities")

    # ------------------------------------------------------------------
    # State change handler — debounced
    # ------------------------------------------------------------------

    def _on_state_change(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        # Cancel any pending debounce
        if self._debounce_handle is not None:
            try:
                self.cancel_timer(self._debounce_handle)
            except Exception:
                pass
        self._debounce_handle = self.run_in(self._resolve_mode, DEBOUNCE_SEC)

    # ------------------------------------------------------------------
    # Mode resolution
    # ------------------------------------------------------------------

    def _resolve_mode(self, kwargs):
        self._debounce_handle = None
        mode = self._compute_mode()
        ha_write(self, BACKEND_ENTITY, mode, {"friendly_name": "EV Active Mode (backend)"})
        self.log(f"EVModeResolver: mode = {mode}")

    def _compute_mode(self):
        # ── Read all relevant entity states ──────────────────────────────
        enabled     = self._is_on("input_boolean.ev_smart_charge_enabled")
        charge_now  = self._is_on("input_boolean.ev_charge_now_override")
        min_chg     = self._is_on("input_boolean.ev_min_charge_enabled")
        should      = self._is_on("binary_sensor.ev_should_charge_now_combined")
        warn        = self._is_on("binary_sensor.ev_forecast_reliability_warning")
        journey     = self._is_on("input_boolean.ev_journey_mode_enabled")
        car_home    = self._is_on("binary_sensor.ev_car_home")

        soc         = self._float("sensor.smart_battery", default=100.0)
        min_soc     = self._float("input_number.ev_min_soc_pct", default=30.0)
        forecast_soc = self._float("sensor.ev_learning_forecast_soc", default=0.0)
        bargain_thr  = self._float("input_number.ev_bargain_price_threshold", default=5.5)

        cmode       = self.get_state("sensor.zag063912_charger_mode") or "unknown"
        not_plugged = cmode.lower() in NOT_PLUGGED_MODES

        # Current spot price in c/kWh for bargain detection
        current_price_ckwh = self._current_price_ckwh()

        # Minutes remaining to deadline
        mins_to_deadline = self._mins_to_deadline()

        # ── P1: Warning ──────────────────────────────────────────────────
        if warn:
            return "warning"

        # ── P2: Not plugged in ───────────────────────────────────────────
        if car_home and not_plugged and soc < min_soc:
            # not_plugged_crit when deadline is critically close (< 30 min)
            if mins_to_deadline is not None and 0 < mins_to_deadline < CRIT_WINDOW_MIN:
                return "not_plugged_crit"
            return "not_plugged"

        # ── P3: Charge Now — manual override ─────────────────────────────
        if charge_now:
            return "charge_now"

        # ── P4: Journey Active — car away while journey mode set ──────────
        if journey and not car_home:
            return "journey_active"

        # ── P5: Min Charge Overlay ───────────────────────────────────────
        if min_chg and should:
            return "min_charge"

        # ── P6: Smart Charging — planned slot active ──────────────────────
        if enabled and should:
            return "smart_charging"

        # ── P7: Smart sub-states ─────────────────────────────────────────
        if enabled:
            # P7a: Journey charging — journey set, car home, plugged in
            if journey and car_home and not not_plugged:
                return "smart_journey"

            # P7b: Bargain fill — spot price below user threshold
            if (current_price_ckwh is not None
                    and current_price_ckwh > 0
                    and current_price_ckwh <= bargain_thr):
                return "smart_bargain"

            # P7c: Sufficient — SOC already covers predicted daily need
            if forecast_soc > 0 and soc >= forecast_soc:
                return "smart_sufficient"

            # P7d: Waiting — default smart sub-state
            return "smart_waiting"

        # ── P8: Disabled ─────────────────────────────────────────────────
        return "disabled"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _is_on(self, entity_id):
        return self.get_state(entity_id) in ("on", "true", "True", True)

    def _float(self, entity_id, default=0.0):
        try:
            v = self.get_state(entity_id)
            if v in (None, "unknown", "unavailable", ""):
                return default
            return float(v)
        except (TypeError, ValueError):
            return default

    def _current_price_ckwh(self):
        """
        Find the PriceWithTax for the current 15-min slot from
        sensor.ev_price_slots_15m_effective data attribute.
        Returns price in c/kWh, or None if unavailable.
        """
        try:
            data = self.get_state(
                "sensor.ev_price_slots_15m_effective", attribute="data"
            )
            if not data:
                return None
            now_ts = int(datetime.now(timezone.utc).timestamp())
            slot_ts = (now_ts // 900) * 900  # floor to current 15-min slot
            from datetime import timezone as tz
            for entry in data:
                dt_str = entry.get("DateTime", "")
                if not dt_str:
                    continue
                try:
                    entry_ts = int(
                        datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        .astimezone(timezone.utc)
                        .timestamp()
                    )
                except (ValueError, AttributeError):
                    continue
                if entry_ts == slot_ts:
                    price_eur = float(entry.get("PriceWithTax", 0))
                    return round(price_eur * 100, 4)  # EUR/kWh → c/kWh
        except Exception as exc:
            self.log(f"EVModeResolver: price lookup failed: {exc}", level="WARNING")
        return None

    def _mins_to_deadline(self):
        """
        Return minutes until input_datetime.ev_deadline, or None if unavailable.
        """
        try:
            dl = self.get_state("input_datetime.ev_deadline")
            if not dl or dl in ("unknown", "unavailable"):
                return None
            dl_dt = datetime.fromisoformat(dl)
            if dl_dt.tzinfo is None:
                dl_dt = dl_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return (dl_dt - now).total_seconds() / 60
        except Exception:
            return None
