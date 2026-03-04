"""
chart_data_writer.py — EV Chart Data Writer
Assembles chart_data.json for the EV dashboard UI and writes it to
/config/www/ev-dashboard/chart_data.json.

Data sources (DESIGN_SPEC Section 6.5):
  price_curve:         sensor.ev_price_slots_15m_effective attribute 'data'
                       Each entry: {DateTime: ISO, Rank: int, PriceWithTax: EUR/kWh}
                       → converted to {hour_offset: float, price_ckwh: float}
  planned_slots:       sensor.ev_plan_15m_rank_effective attribute 'planned_slots'
                       Each entry: {ts: unix_ts, rank: int}
  reliability_cutoff:  sensor.ev_pricing_horizon_end_ts (Unix timestamp)
                       → day offset from now

After writing, updates input_text.ev_chart_data_updated with the ISO timestamp.
The template sensor sensor.ev_chart_data_updated reads from the input_text backend.
"""

import json
import os
from datetime import datetime, timezone

import appdaemon.plugins.hass.hassapi as hass

from utils import ha_write

CHART_DATA_PATH   = "/config/www/ev-dashboard/chart_data.json"
BACKEND_ENTITY    = "input_text.ev_chart_data_updated"

# Entities that trigger a chart data refresh
WATCHED_ENTITIES = [
    "sensor.ev_price_slots_15m_effective",
    "sensor.ev_plan_15m_rank_effective",
    "sensor.ev_pricing_horizon_end_ts",
]

# Refresh every 15 minutes regardless of state changes (keeps data fresh)
REFRESH_INTERVAL_SEC = 900

# Max price curve horizon: keep up to 8 days of 15-min slots
MAX_PRICE_HOURS = 192  # 8 days × 24h

DEBOUNCE_SEC = 5  # slightly longer debounce — file I/O is heavier


class EVChartDataWriter(hass.Hass):

    def initialize(self):
        self.log("EVChartDataWriter: initialising")
        self._debounce_handle = None

        # Ensure output directory exists
        os.makedirs(os.path.dirname(CHART_DATA_PATH), exist_ok=True)

        for entity in WATCHED_ENTITIES:
            self.listen_state(self._on_state_change, entity)

        self.run_every(self._refresh, "now", REFRESH_INTERVAL_SEC)
        self.log(f"EVChartDataWriter: watching entities, refreshing every "
                 f"{REFRESH_INTERVAL_SEC}s")

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
        self._debounce_handle = self.run_in(self._refresh, DEBOUNCE_SEC)

    def _refresh(self, kwargs):
        self._debounce_handle = None
        try:
            data = self._build_chart_data()
            self._write_json(data)
            ts = datetime.now(timezone.utc).isoformat()
            ha_write(self, BACKEND_ENTITY, ts,
                     {"friendly_name": "EV Chart Data Updated (backend)"})
            self.log(f"EVChartDataWriter: wrote chart_data.json — "
                     f"{len(data.get('price_curve', []))} price points, "
                     f"{len(data.get('planned_slots', []))} planned slots")
        except Exception as exc:
            self.log(f"EVChartDataWriter: refresh failed: {exc}", level="ERROR")

    # ------------------------------------------------------------------
    # Chart data assembly
    # ------------------------------------------------------------------

    def _build_chart_data(self):
        now_ts = datetime.now(timezone.utc).timestamp()

        price_curve    = self._build_price_curve(now_ts)
        planned_slots  = self._build_planned_slots()
        reliability_cutoff_day = self._reliability_cutoff_day(now_ts)

        return {
            "generated_at":          datetime.now(timezone.utc).isoformat(),
            "price_curve":           price_curve,
            "planned_slots":         planned_slots,
            "reliability_cutoff_day": reliability_cutoff_day,
        }

    def _build_price_curve(self, now_ts):
        """
        Build price_curve array from sensor.ev_price_slots_15m_effective.
        Returns [{hour_offset: float, price_ckwh: float}, ...] sorted by hour_offset.
        Only includes slots up to MAX_PRICE_HOURS hours from now.
        """
        try:
            data = self.get_state(
                "sensor.ev_price_slots_15m_effective", attribute="data"
            )
            if not data:
                self.log("EVChartDataWriter: no price data available", level="WARNING")
                return []

            curve = []
            cutoff_ts = now_ts + MAX_PRICE_HOURS * 3600

            for entry in data:
                dt_str = entry.get("DateTime", "")
                if not dt_str:
                    continue
                try:
                    slot_dt = datetime.fromisoformat(
                        dt_str.replace("Z", "+00:00")
                    ).astimezone(timezone.utc)
                    slot_ts = slot_dt.timestamp()
                except (ValueError, AttributeError):
                    continue

                if slot_ts > cutoff_ts:
                    continue  # beyond horizon

                hour_offset = round((slot_ts - now_ts) / 3600, 4)
                price_eur   = float(entry.get("PriceWithTax", 0))
                price_ckwh  = round(price_eur * 100, 4)

                curve.append({
                    "hour_offset": hour_offset,
                    "price_ckwh":  price_ckwh,
                })

            curve.sort(key=lambda x: x["hour_offset"])
            return curve

        except Exception as exc:
            self.log(f"EVChartDataWriter: price curve build failed: {exc}",
                     level="WARNING")
            return []

    def _build_planned_slots(self):
        """
        Build planned_slots array from sensor.ev_plan_15m_rank_effective.
        Returns [{ts: unix_ts, rank: int}, ...] sorted by ts.
        """
        try:
            slots = self.get_state(
                "sensor.ev_plan_15m_rank_effective", attribute="planned_slots"
            )
            if not slots:
                return []

            result = []
            for s in slots:
                ts   = int(s.get("ts", 0))
                rank = int(s.get("rank", 0))
                if ts > 0:
                    result.append({"ts": ts, "rank": rank})

            result.sort(key=lambda x: x["ts"])
            return result

        except Exception as exc:
            self.log(f"EVChartDataWriter: planned slots build failed: {exc}",
                     level="WARNING")
            return []

    def _reliability_cutoff_day(self, now_ts):
        """
        Return the reliability cutoff as a day offset from today (float).
        Uses sensor.ev_pricing_horizon_end_ts (Unix timestamp).
        Returns None if unavailable.
        """
        try:
            cutoff_state = self.get_state("sensor.ev_pricing_horizon_end_ts")
            if not cutoff_state or cutoff_state in ("unknown", "unavailable"):
                return None
            cutoff_ts = float(cutoff_state)
            # Convert to day offset from start of today
            today_start = datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0
            ).timestamp()
            return round((cutoff_ts - today_start) / 86400, 2)
        except (TypeError, ValueError, Exception) as exc:
            self.log(f"EVChartDataWriter: reliability cutoff failed: {exc}",
                     level="WARNING")
            return None

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def _write_json(self, data):
        tmp_path = CHART_DATA_PATH + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp_path, CHART_DATA_PATH)  # atomic rename
