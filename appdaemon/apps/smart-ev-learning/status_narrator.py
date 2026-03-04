"""
status_narrator.py — EV Status Narrator
Watches mode entities and produces sensor.ev_status_narrative via
input_text.ev_status_narrative (AppDaemon → HA conflict-free pattern).

Implements the complete DESIGN_SPEC Section 5.1 narrative string table.
Journey tier logic:
  Tier 1 (silent):   journey set + car plugged in → charging toward 100%
  Tier 2 (prompt):   journey set + not plugged + cheap slots tonight → plug-in prompt
  Tier 3 (warning):  departure within 12h + 100% physically unreachable → one warning

Battery specs (Smart #1, confirmed Session 0):
  Capacity: 62.0 kWh, max charge rate: ~11.04 kW
"""

from datetime import datetime, timezone

import appdaemon.plugins.hass.hassapi as hass

from utils import ha_write

BACKEND_ENTITY = "input_text.ev_status_narrative"

BATTERY_KWH   = 62.0
MAX_CHARGE_KW = 11.0  # Zaptec max 16A × 3ph × 230V

NOT_PLUGGED_MODES = {"disconnected", "unknown"}

# Entities that trigger a narrative refresh
WATCHED_ENTITIES = [
    "input_text.ev_active_mode",   # react immediately when mode changes
    "sensor.smart_battery",
    "binary_sensor.ev_should_charge_now_combined",
    "binary_sensor.ev_forecast_reliability_warning",
    "binary_sensor.ev_car_home",
    "input_boolean.ev_smart_charge_enabled",
    "input_boolean.ev_journey_mode_enabled",
    "sensor.zag063912_charger_mode",
    "input_datetime.ev_deadline",
    "input_datetime.ev_departure_time",
    "sensor.ev_learning_forecast_soc",
    "input_number.ev_bargain_price_threshold",
    "sensor.ev_plan_15m_rank_effective",
    "sensor.ev_plan_cycle_cost",     # shortfall attributes (Session 3+)
]

DEBOUNCE_SEC = 2


class EVStatusNarrator(hass.Hass):

    def initialize(self):
        self.log("EVStatusNarrator: initialising")
        self._debounce_handle = None
        self._tier3_fired = False  # Tier 3 warning fires only once per departure

        for entity in WATCHED_ENTITIES:
            self.listen_state(self._on_state_change, entity)

        # Run every 5 minutes for time-dependent strings (slot times, deadlines)
        self.run_every(self._update, "now", 300)
        self.log("EVStatusNarrator: watching entities and running every 5 min")

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_state_change(self, entity, attribute, old, new, kwargs):
        if old == new:
            return
        if self._debounce_handle is not None:
            try:
                self.cancel_timer(self._debounce_handle)
            except Exception:
                pass
        self._debounce_handle = self.run_in(self._update, DEBOUNCE_SEC)

    def _update(self, kwargs):
        self._debounce_handle = None
        narrative = self._build_narrative()
        # HA input_text max 255 chars
        narrative = narrative[:255]
        ha_write(self, BACKEND_ENTITY, narrative,
                 {"friendly_name": "EV Status Narrative (backend)"})

    # ------------------------------------------------------------------
    # Narrative logic — mirrors Section 5.1 table exactly
    # ------------------------------------------------------------------

    def _build_narrative(self):
        mode = self.get_state("input_text.ev_active_mode") or "disabled"

        # ── Warning modes ─────────────────────────────────────────────
        if mode == "warning":
            return "Forecast data unreliable. Check smart charging settings."

        if mode == "shortfall":
            extra_slots = self._attr_int("sensor.ev_plan_cycle_cost", "extra_slots_added", 0)
            if extra_slots > 0:
                return f"Session running slow. Added {extra_slots} extra slots — cost may be slightly higher."
            return "Shortfall cannot be recovered. Car may not reach target by deadline."

        # ── Not Plugged In ────────────────────────────────────────────
        if mode in ("not_plugged", "not_plugged_crit"):
            soc = self._float("sensor.smart_battery", default=0.0)
            dl_str = self._deadline_str()
            mins = self._mins_to_deadline()
            if mode == "not_plugged_crit":
                return "Plug in now — less than 30 minutes to meet minimum charge."
            if mins is not None and mins > 0:
                return f"Please plug in before {dl_str} to meet tomorrow's target."
            # SOC sufficient — not urgent
            return f"{int(round(soc))}% — no charge needed today. Plug in when convenient."

        # ── Charge Now ────────────────────────────────────────────────
        if mode == "charge_now":
            # detect late plug-in (past deadline)
            mins = self._mins_to_deadline()
            if mins is not None and mins < 0:
                plug_time = datetime.now(timezone.utc).strftime("%H:%M")
                dl_str = self._deadline_str()
                soc = int(round(self._float("sensor.smart_battery", 0)))
                return (f"Plugged in at {plug_time}. Cannot reach {soc}% by "
                        f"{dl_str} — charging now.")
            return "Charging at maximum current. Smart plan is bypassed."

        # ── Journey Active (car away) ──────────────────────────────────
        if mode == "journey_active":
            # Check if arrival projected SOC is low
            arrival_soc = self._float("sensor.ev_projected_arrival_soc", default=-1.0)
            if 0 <= arrival_soc < 35:
                return (f"Arriving home ~{int(arrival_soc)}% — tight for tomorrow. "
                        f"DC top-up recommended.")
            return "Journey active. System resumes when you return home."

        # ── Min Charge ────────────────────────────────────────────────
        if mode == "min_charge":
            dl_str = self._deadline_str()
            return f"Min charge running alongside smart plan. On track for {dl_str}."

        # ── Smart sub-states ──────────────────────────────────────────
        if mode == "smart_charging":
            slot_str, price_str = self._current_slot_str()
            if slot_str:
                return f"Charging now — slot {slot_str} at {price_str} c/kWh."
            return "Charging now in planned slot."

        if mode == "smart_journey":
            # Tier 1: journey set, plugged in — silently charging toward 100%
            jrny_day = self._departure_day_str()
            dl_str = self._deadline_str()
            return f"Journey {jrny_day} — charging toward 100% using cheap slots."

        if mode == "smart_bargain":
            price_str = self._current_spot_price_str()
            return f"Filling to ceiling — spot price dropped to {price_str} c/kWh."

        if mode == "smart_sufficient":
            soc = int(round(self._float("sensor.smart_battery", 67.0)))
            # Try to name the day range from forecast
            forecast_days = self._float_attr(
                "sensor.ev_consumption_forecast", "forecast_reliable_days", 3.0
            )
            day_label = self._days_label(int(forecast_days))
            return f"{soc}% — sufficient for predicted driving until {day_label}."

        if mode == "smart_waiting":
            # Check Journey Tier 2: journey set, not plugged in, cheap slots tonight
            journey = self._is_on("input_boolean.ev_journey_mode_enabled")
            car_home = self._is_on("binary_sensor.ev_car_home")
            cmode = (self.get_state("sensor.zag063912_charger_mode") or "").lower()
            not_plugged = cmode in NOT_PLUGGED_MODES

            if journey and car_home and not_plugged:
                jrny_day = self._departure_day_str()
                # Check Journey Tier 3: departure within 12h, 100% unreachable
                if self._tier3_condition():
                    if not self._tier3_fired:
                        self._tier3_fired = True
                        return (f"Journey {jrny_day} — not enough time to reach 100%. "
                                f"Consider Charge Now.")
                    # Tier 3 already fired — fall through to standard Tier 2 message
                else:
                    self._tier3_fired = False  # reset when no longer in Tier 3 window
                return (f"Journey {jrny_day} — cheap slots tonight. "
                        f"Plug in to start charging.")

            # Standard smart_waiting
            dl_str = self._deadline_str()
            if dl_str:
                return f"Waiting for tonight's cheap slot. On track for {dl_str}."
            return "Waiting for next cheap slot."

        # ── Disabled ──────────────────────────────────────────────────
        if mode == "disabled":
            enabled = self._is_on("input_boolean.ev_smart_charge_enabled")
            if not enabled:
                return "Smart charging is off. Charger will not run automatically."
            # ev_learning has insufficient data
            return "Limited data for this scenario — charging conservatively."

        # Fallback for any unrecognised mode
        return "Smart charging is off. Charger will not run automatically."

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

    def _float_attr(self, entity_id, attr, default=0.0):
        try:
            v = self.get_state(entity_id, attribute=attr)
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def _attr_int(self, entity_id, attr, default=0):
        try:
            v = self.get_state(entity_id, attribute=attr)
            return int(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    def _deadline_str(self):
        """Return deadline time as 'HH:MM' or empty string."""
        try:
            dl = self.get_state("input_datetime.ev_deadline")
            if not dl or dl in ("unknown", "unavailable"):
                return ""
            dt = datetime.fromisoformat(dl)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_dt = dt.astimezone()
            return local_dt.strftime("%H:%M")
        except Exception:
            return ""

    def _mins_to_deadline(self):
        """Minutes to deadline, or None."""
        try:
            dl = self.get_state("input_datetime.ev_deadline")
            if not dl or dl in ("unknown", "unavailable"):
                return None
            dt = datetime.fromisoformat(dl)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (dt - datetime.now(timezone.utc)).total_seconds() / 60
        except Exception:
            return None

    def _departure_day_str(self):
        """Return departure day as weekday name (e.g. 'Thursday') or 'soon'."""
        try:
            dep = self.get_state("input_datetime.ev_departure_time")
            if not dep or dep in ("unknown", "unavailable"):
                return "soon"
            dt = datetime.fromisoformat(dep)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone().strftime("%A")
        except Exception:
            return "soon"

    def _current_slot_str(self):
        """Return (slot_time_range_str, price_ckwh_str) for the current active slot."""
        try:
            slots = self.get_state(
                "sensor.ev_plan_15m_rank_effective", attribute="planned_slots"
            )
            if not slots:
                return "", ""
            now_ts = int(datetime.now(timezone.utc).timestamp())
            slot_ts = (now_ts // 900) * 900
            for s in slots:
                ts = int(s.get("ts", 0))
                if ts == slot_ts:
                    start = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
                    end   = datetime.fromtimestamp(ts + 900, tz=timezone.utc).astimezone()
                    price_raw = float(s.get("price", 0))
                    # Normalise to c/kWh
                    if price_raw > 2:
                        price_ckwh = round(price_raw, 1)
                    else:
                        price_ckwh = round(price_raw * 100, 1)
                    return (
                        f"{start.strftime('%H:%M')}–{end.strftime('%H:%M')}",
                        str(price_ckwh),
                    )
        except Exception:
            pass
        return "", ""

    def _current_spot_price_str(self):
        """Return current spot price as 'X.X c/kWh' string."""
        try:
            data = self.get_state(
                "sensor.ev_price_slots_15m_effective", attribute="data"
            )
            if not data:
                return "?"
            now_ts = int(datetime.now(timezone.utc).timestamp())
            slot_ts = (now_ts // 900) * 900
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
                    return str(round(price_eur * 100, 1))
        except Exception:
            pass
        return "?"

    def _days_label(self, days_ahead):
        """Return a day label like 'Thursday' for N days ahead."""
        try:
            from datetime import timedelta
            target = datetime.now(timezone.utc) + timedelta(days=days_ahead)
            return target.astimezone().strftime("%A")
        except Exception:
            return "the week"

    def _tier3_condition(self):
        """
        True if departure is within 12h AND 100% SOC is physically unreachable
        at max AC charge rate.
        """
        try:
            dep = self.get_state("input_datetime.ev_departure_time")
            if not dep or dep in ("unknown", "unavailable"):
                return False
            dt = datetime.fromisoformat(dep)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            hours_to_dep = (dt - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_to_dep > 12 or hours_to_dep < 0:
                return False
            soc = self._float("sensor.smart_battery", 100.0)
            needed_kwh = (100.0 - soc) / 100.0 * BATTERY_KWH
            max_deliverable_kwh = hours_to_dep * MAX_CHARGE_KW
            return max_deliverable_kwh < needed_kwh
        except Exception:
            return False
