"""
session_monitor.py — EV Plan Cycle & Monthly Cost Tracker

Three responsibilities (DESIGN_SPEC Sections 10, 11):

1. Plan cycle tracking — detect plug-in as cycle start, accumulate cost/kWh from
   ev_session_cost_v3 entities across multiple sessions within the cycle, write
   sensor.ev_plan_cycle_cost (via input_text.ev_plan_cycle_cost) at cycle end.
   Persist history to /config/plan_cycle_data.json.

2. kWh checkpoints (50% / 75%) — during an active session compare actual delivery
   against expected rate.
   • 50% checkpoint: if >15% behind → activate input_boolean.ev_charge_now_override
     (uses same ev_control_effective_master activation path) + record extra_slots.
   • 75% checkpoint: if unrecoverable → set input_boolean.ev_shortfall_unrecoverable
     so mode_resolver.py escalates to warning + send push notification.

3. Monthly summary — at the start of each month read plan_cycle_data.json, compute
   previous-month totals, write sensor.ev_monthly_cost_summary
   (via input_text.ev_monthly_cost_summary).

Reads from ev_session_cost_v3 entities — does NOT duplicate their logic:
  binary_sensor.ev3_session_active       — session on/off boundary
  sensor.ev3_session_delivered_kwh       — kWh delivered in current session (resets each session)
  input_number.ev3_session_cost_actual_eur — EUR accumulated in current session (resets each session)
"""

import json
import os
import time
from datetime import datetime, timezone

import appdaemon.plugins.hass.hassapi as hass

from utils import ha_write

# ── Backend entities (input_text pass-through pattern) ──────────────────────
BACKEND_CYCLE_COST   = "input_text.ev_plan_cycle_cost"
BACKEND_MONTHLY      = "input_text.ev_monthly_cost_summary"

# ── ev3 source entities (read only — never write to these) ──────────────────
E_SESSION_ACTIVE     = "binary_sensor.ev3_session_active"
E_DELIVERED_KWH      = "sensor.ev3_session_delivered_kwh"
E_SESSION_COST       = "input_number.ev3_session_cost_actual_eur"

# ── Other entities read / written ────────────────────────────────────────────
E_TARGET_KWH         = "input_number.ev_target_kwh"
E_REMAINING_KWH      = "sensor.ev_remaining_used_kwh"
E_CHARGE_KW          = "input_number.ev_assumed_charge_kw"
E_DEADLINE           = "input_datetime.ev_deadline"
E_CHARGE_NOW         = "input_boolean.ev_charge_now_override"
E_SHORTFALL_WARN     = "input_boolean.ev_shortfall_unrecoverable"
E_PLAN_SLOTS         = "sensor.ev_plan_15m_rank_effective"

DATA_FILE = "/config/plan_cycle_data.json"

# Checkpoint thresholds
CHECKPOINT_50_PCT  = 0.50
CHECKPOINT_75_PCT  = 0.75
SHORTFALL_THRESHOLD = 0.15  # >15% below expected capacity → recovery


class EVSessionMonitor(hass.Hass):

    def initialize(self):
        self.log("EVSessionMonitor: initialising")

        # Load persisted cycle state from JSON
        self._data = self._load_data()

        # Listen for session start / end
        self.listen_state(self._on_session_change, E_SESSION_ACTIVE)

        # Listen for energy delivery updates (checkpoint logic)
        self.listen_state(self._on_energy_change, E_DELIVERED_KWH)

        # Monthly summary check — run at 00:05 every day; handler checks if 1st of month
        self.run_daily(self._check_monthly_boundary, "00:05:00")

        # Write current sensor state on startup
        self._write_cycle_cost_sensor()
        self._write_monthly_summary_sensor()

        self.log("EVSessionMonitor: ready — cycle_active=%s", self._data["current_cycle"]["active"])

    # ─────────────────────────────────────────────────────────────────────────
    # Session lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def _on_session_change(self, entity, attribute, old, new, kwargs):
        if new == old:
            return
        if new == "on":
            self._on_session_start()
        elif new == "off" and old == "on":
            self._on_session_end()

    def _on_session_start(self):
        """Car plugged in — start or continue a plan cycle."""
        cycle = self._data["current_cycle"]
        now_iso = datetime.now(timezone.utc).isoformat()

        if not cycle["active"]:
            # New plan cycle
            cycle["active"]              = True
            cycle["start_time"]          = now_iso
            cycle["prev_sessions_kwh"]   = 0.0
            cycle["prev_sessions_cost"]  = 0.0
            cycle["extra_slots_cost"]    = 0.0
            cycle["checkpoint_50_done"]  = False
            cycle["checkpoint_75_done"]  = False
            cycle["shortfall_active"]    = False
            self.log("EVSessionMonitor: new plan cycle started at %s", now_iso)
        else:
            self.log("EVSessionMonitor: continuing plan cycle (new session within cycle)")

        # Reset per-session tracking
        cycle["session_start_delivered"] = self._float(E_DELIVERED_KWH, 0.0)
        cycle["session_start_cost"]      = self._float(E_SESSION_COST, 0.0)

        self._save_data()
        self._write_cycle_cost_sensor()

    def _on_session_end(self):
        """Charger disconnected — accumulate and check cycle completion."""
        cycle = self._data["current_cycle"]
        if not cycle["active"]:
            return

        # How much was delivered in this session?
        session_kwh  = max(0.0, self._float(E_DELIVERED_KWH, 0.0))
        session_cost = max(0.0, self._float(E_SESSION_COST, 0.0))

        cycle["prev_sessions_kwh"]  += session_kwh
        cycle["prev_sessions_cost"] += session_cost

        self.log(
            "EVSessionMonitor: session ended — session_kwh=%.3f session_cost=%.4f "
            "cycle_kwh=%.3f cycle_cost=%.4f",
            session_kwh, session_cost,
            cycle["prev_sessions_kwh"], cycle["prev_sessions_cost"],
        )

        # Check cycle completion: target reached?
        remaining = self._float(E_REMAINING_KWH, 999.0)
        if remaining <= 0.05:
            self._end_cycle(reason="target_reached")
            return

        # Check cycle completion: deadline passed?
        if self._deadline_passed():
            self._end_cycle(reason="deadline_passed")
            return

        # Otherwise: cycle continues (car may plug in again)
        self._save_data()
        self._write_cycle_cost_sensor()

    # ─────────────────────────────────────────────────────────────────────────
    # kWh checkpoint logic
    # ─────────────────────────────────────────────────────────────────────────

    def _on_energy_change(self, entity, attribute, old, new, kwargs):
        """Called on every ev3_session_delivered_kwh change."""
        cycle = self._data["current_cycle"]
        if not cycle["active"]:
            return

        current_session_kwh = self._float(E_DELIVERED_KWH, 0.0)
        total_delivered = cycle["prev_sessions_kwh"] + current_session_kwh
        target_kwh = self._float(E_TARGET_KWH, 0.0)

        if target_kwh <= 0:
            return

        pct = total_delivered / target_kwh

        # ── 50% checkpoint ──────────────────────────────────────────────
        if not cycle["checkpoint_50_done"] and pct >= CHECKPOINT_50_PCT:
            cycle["checkpoint_50_done"] = True
            self._check_shortfall_50(total_delivered, target_kwh)
            self._save_data()

        # ── 75% checkpoint ──────────────────────────────────────────────
        if not cycle["checkpoint_75_done"] and pct >= CHECKPOINT_75_PCT:
            cycle["checkpoint_75_done"] = True
            self._check_shortfall_75(total_delivered, target_kwh)
            self._save_data()

    def _check_shortfall_50(self, delivered, target):
        """
        50% checkpoint: check if available planned slots can cover remaining kWh.
        If capacity is >15% short → activate charge_now_override.
        """
        remaining = max(0.0, target - delivered)
        if remaining <= 0:
            return

        available_kwh = self._available_plan_kwh()
        if available_kwh < 0:
            return  # can't determine — skip

        shortfall_ratio = (remaining - available_kwh) / remaining if remaining > 0 else 0.0

        if shortfall_ratio > SHORTFALL_THRESHOLD:
            self.log(
                "EVSessionMonitor: 50%% checkpoint — shortfall detected "
                "(available=%.3f remaining=%.3f ratio=%.2f). Activating charge_now_override.",
                available_kwh, remaining, shortfall_ratio,
            )
            try:
                self.call_service("input_boolean/turn_on", entity_id=E_CHARGE_NOW)
            except Exception as exc:
                self.log("EVSessionMonitor: failed to activate charge_now_override: %s", exc, level="WARNING")

            # Mark that we triggered shortfall recovery for extra cost tracking
            self._data["current_cycle"]["shortfall_active"] = True
            # Schedule auto-clear of charge_now_override after 2 hours
            self.run_in(self._clear_shortfall_override, 7200)
        else:
            self.log(
                "EVSessionMonitor: 50%% checkpoint — on track "
                "(available=%.3f remaining=%.3f)",
                available_kwh, remaining,
            )

    def _check_shortfall_75(self, delivered, target):
        """
        75% checkpoint: if shortfall is still unrecoverable → escalate to warning.
        'Unrecoverable' means available slot capacity < 50% of remaining target.
        """
        remaining = max(0.0, target - delivered)
        if remaining <= 0:
            return

        available_kwh = self._available_plan_kwh()
        if available_kwh < 0:
            return  # can't determine

        if available_kwh < remaining * 0.50:
            self.log(
                "EVSessionMonitor: 75%% checkpoint — UNRECOVERABLE shortfall "
                "(available=%.3f remaining=%.3f). Escalating to warning.",
                available_kwh, remaining,
            )
            # Set shortfall warning flag — mode_resolver.py watches this
            ha_write(self, E_SHORTFALL_WARN, "on", {"friendly_name": "EV Shortfall Unrecoverable"})

            # Push notification to household devices
            projected_soc = self._projected_soc_at_deadline(delivered, available_kwh, target)
            msg = (
                f"EV charge shortfall — projected SOC at deadline: {projected_soc:.0f}%. "
                f"Cannot recover {remaining - available_kwh:.1f} kWh in remaining slots."
            )
            try:
                self.call_service(
                    "notify/mobile_app_all_devices",
                    title="EV Charge Warning",
                    message=msg,
                )
            except Exception as exc:
                self.log("EVSessionMonitor: push notification failed: %s", exc, level="WARNING")
        else:
            self.log(
                "EVSessionMonitor: 75%% checkpoint — recoverable "
                "(available=%.3f remaining=%.3f)",
                available_kwh, remaining,
            )

    def _clear_shortfall_override(self, kwargs):
        """Auto-clear charge_now_override after shortfall recovery window."""
        cycle = self._data["current_cycle"]
        if not cycle.get("shortfall_active"):
            return
        remaining = self._float(E_REMAINING_KWH, 999.0)
        if remaining <= 0.05:
            return  # target reached — auto-off already handled
        try:
            self.call_service("input_boolean/turn_off", entity_id=E_CHARGE_NOW)
            self.log("EVSessionMonitor: shortfall recovery window ended, cleared charge_now_override")
        except Exception as exc:
            self.log("EVSessionMonitor: failed to clear charge_now_override: %s", exc, level="WARNING")

    # ─────────────────────────────────────────────────────────────────────────
    # Cycle completion
    # ─────────────────────────────────────────────────────────────────────────

    def _end_cycle(self, reason="unknown"):
        """Finalise the plan cycle, write sensor, persist to JSON, reset state."""
        cycle = self._data["current_cycle"]
        now_iso = datetime.now(timezone.utc).isoformat()

        total_kwh  = cycle.get("prev_sessions_kwh", 0.0)
        total_cost = cycle.get("prev_sessions_cost", 0.0)
        extra_cost = cycle.get("extra_slots_cost", 0.0)

        avg_price = 0.0
        if total_kwh > 0:
            avg_price = round((total_cost / total_kwh) * 100, 2)  # EUR/kWh → c/kWh

        completed = {
            "start_time":         cycle.get("start_time", "unknown"),
            "end_time":           now_iso,
            "end_reason":         reason,
            "total_kwh":          round(total_kwh, 3),
            "total_cost_eur":     round(total_cost, 4),
            "avg_price_ckwh":     avg_price,
            "extra_slots_cost_eur": round(extra_cost, 4),
            "month":              datetime.now(timezone.utc).strftime("%Y-%m"),
        }
        self._data["completed_cycles"].append(completed)

        self.log(
            "EVSessionMonitor: cycle COMPLETE — reason=%s kwh=%.3f cost=%.4f EUR avg=%.2f c/kWh",
            reason, total_kwh, total_cost, avg_price,
        )

        # Write final plan cycle sensor
        ha_write(
            self, BACKEND_CYCLE_COST, reason,
            {
                "friendly_name":       "EV Plan Cycle Cost (backend)",
                "start_time":          completed["start_time"],
                "end_time":            completed["end_time"],
                "total_kwh":           completed["total_kwh"],
                "total_cost_eur":      completed["total_cost_eur"],
                "avg_price_ckwh":      completed["avg_price_ckwh"],
                "extra_slots_cost_eur": completed["extra_slots_cost_eur"],
            },
        )

        # Clear shortfall warning if it was set
        try:
            self.call_service("input_boolean/turn_off", entity_id=E_SHORTFALL_WARN)
        except Exception:
            pass

        # Reset current cycle
        self._data["current_cycle"] = self._empty_cycle()
        self._save_data()

    def _write_cycle_cost_sensor(self):
        """Write current (in-progress) cycle state to the backend entity."""
        cycle = self._data["current_cycle"]
        if not cycle["active"]:
            return

        total_kwh  = cycle.get("prev_sessions_kwh", 0.0)
        total_cost = cycle.get("prev_sessions_cost", 0.0)
        avg_price  = round((total_cost / total_kwh) * 100, 2) if total_kwh > 0 else 0.0

        ha_write(
            self, BACKEND_CYCLE_COST, "active",
            {
                "friendly_name":       "EV Plan Cycle Cost (backend)",
                "start_time":          cycle.get("start_time", "unknown"),
                "end_time":            "active",
                "total_kwh":           round(total_kwh, 3),
                "total_cost_eur":      round(total_cost, 4),
                "avg_price_ckwh":      avg_price,
                "extra_slots_cost_eur": round(cycle.get("extra_slots_cost", 0.0), 4),
            },
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Monthly summary
    # ─────────────────────────────────────────────────────────────────────────

    def _check_monthly_boundary(self, kwargs):
        """Called daily at 00:05. On the 1st of the month compute previous month summary."""
        today = datetime.now(timezone.utc)
        if today.day != 1:
            return

        # Previous month
        if today.month == 1:
            prev_year, prev_month = today.year - 1, 12
        else:
            prev_year, prev_month = today.year, today.month - 1

        month_key = f"{prev_year}-{prev_month:02d}"
        self._compute_monthly_summary(month_key)

    def _compute_monthly_summary(self, month_key):
        """Aggregate completed cycles for month_key and write the monthly sensor."""
        cycles_in_month = [
            c for c in self._data["completed_cycles"]
            if c.get("month") == month_key
        ]

        total_kwh  = sum(c["total_kwh"] for c in cycles_in_month)
        total_cost = sum(c["total_cost_eur"] for c in cycles_in_month)
        count      = len(cycles_in_month)
        avg_price  = round((total_cost / total_kwh) * 100, 2) if total_kwh > 0 else 0.0

        self._data["monthly_summaries"][month_key] = {
            "month":          month_key,
            "total_kwh":      round(total_kwh, 3),
            "total_cost_eur": round(total_cost, 4),
            "avg_price_ckwh": avg_price,
            "cycle_count":    count,
        }
        self._save_data()
        self._write_monthly_summary_sensor(month_key)
        self.log(
            "EVSessionMonitor: monthly summary %s — %d cycles %.3f kWh %.4f EUR",
            month_key, count, total_kwh, total_cost,
        )

    def _write_monthly_summary_sensor(self, month_key=None):
        """Write the most-recent monthly summary to the backend entity."""
        summaries = self._data.get("monthly_summaries", {})
        if not summaries:
            return

        # Use provided month_key or most recent
        if month_key is None:
            month_key = sorted(summaries.keys())[-1]

        s = summaries.get(month_key)
        if not s:
            return

        ha_write(
            self, BACKEND_MONTHLY, month_key,
            {
                "friendly_name":  "EV Monthly Cost Summary (backend)",
                "month":          s["month"],
                "total_kwh":      s["total_kwh"],
                "total_cost_eur": s["total_cost_eur"],
                "avg_price_ckwh": s["avg_price_ckwh"],
                "cycle_count":    s["cycle_count"],
            },
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _available_plan_kwh(self):
        """
        Estimate kWh deliverable from currently-planned slots.
        Returns -1 if data unavailable.
        """
        try:
            planned = self.get_state(E_PLAN_SLOTS, attribute="planned_slots") or []
            charge_kw = self._float(E_CHARGE_KW, 7.4)
            if charge_kw <= 0 or not planned:
                return -1
            return len(planned) * (charge_kw * 0.25)
        except Exception as exc:
            self.log("EVSessionMonitor: _available_plan_kwh error: %s", exc, level="WARNING")
            return -1

    def _deadline_passed(self):
        """True if the deadline is in the past."""
        try:
            dl = self.get_state(E_DEADLINE)
            if not dl or dl in ("unknown", "unavailable"):
                return False
            dl_dt = datetime.fromisoformat(dl)
            if dl_dt.tzinfo is None:
                dl_dt = dl_dt.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) > dl_dt
        except Exception:
            return False

    def _projected_soc_at_deadline(self, delivered_kwh, available_kwh, target_kwh):
        """Rough SOC projection given current delivery trajectory."""
        try:
            soc_raw = self.get_state("sensor.smart_battery")
            current_soc = float(soc_raw) if soc_raw not in (None, "unknown", "unavailable") else 50.0
            projected_kwh = delivered_kwh + available_kwh
            additional_soc = (projected_kwh / 62.0) * 100.0
            return min(100.0, current_soc + additional_soc)
        except Exception:
            return 0.0

    def _float(self, entity_id, default=0.0):
        try:
            v = self.get_state(entity_id)
            if v in (None, "unknown", "unavailable", ""):
                return default
            return float(v)
        except (TypeError, ValueError):
            return default

    # ─────────────────────────────────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _empty_cycle():
        return {
            "active":                False,
            "start_time":            None,
            "prev_sessions_kwh":     0.0,
            "prev_sessions_cost":    0.0,
            "extra_slots_cost":      0.0,
            "checkpoint_50_done":    False,
            "checkpoint_75_done":    False,
            "shortfall_active":      False,
            "session_start_delivered": 0.0,
            "session_start_cost":    0.0,
        }

    def _load_data(self):
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r") as f:
                    data = json.load(f)
                    # Ensure all keys exist (migration safety)
                    if "current_cycle" not in data:
                        data["current_cycle"] = self._empty_cycle()
                    if "completed_cycles" not in data:
                        data["completed_cycles"] = []
                    if "monthly_summaries" not in data:
                        data["monthly_summaries"] = {}
                    return data
        except Exception as exc:
            self.log("EVSessionMonitor: failed to load %s: %s — starting fresh", DATA_FILE, exc, level="WARNING")

        return {
            "current_cycle":    self._empty_cycle(),
            "completed_cycles": [],
            "monthly_summaries": {},
        }

    def _save_data(self):
        try:
            tmp = DATA_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, DATA_FILE)
        except Exception as exc:
            self.log("EVSessionMonitor: failed to save %s: %s", DATA_FILE, exc, level="ERROR")
