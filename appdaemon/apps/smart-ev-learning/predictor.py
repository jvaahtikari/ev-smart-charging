"""
predictor.py — EV Target SOC Predictor
Reads consumption_model.json and publishes a recommended charge target
to sensor.ev_target_soc in Home Assistant, plus a multi-day consumption
forecast to sensor.ev_consumption_forecast.
"""

import json
import os
from datetime import datetime, date, timedelta

import appdaemon.plugins.hass.hassapi as hass

ENTITY_WEATHER       = "weather.forecast_koti"
ENTITY_INTERIOR_TEMP = "sensor.smart_interior_temperature"

VALID_SEASON_BANDS = {
    "winter":  ["jaakyma", "cold", "near_zero", "cool"],
    "spring":  ["near_zero", "cool", "mild"],
    "summer":  ["mild", "normal", "hot"],
    "autumn":  ["cool", "mild", "near_zero"],
}

SEASON_MONTHS = {
    12: "winter", 1: "winter", 2: "winter",
    3: "spring",  4: "spring", 5: "spring",
    6: "summer",  7: "summer", 8: "summer",
    9: "autumn",  10: "autumn", 11: "autumn",
}

TEMP_BAND_ORDER = ["jaakyma", "cold", "near_zero", "cool", "mild", "normal", "hot"]

TEMP_BANDS = [
    (-999, -15, "jaakyma"),
    (-15,   -5, "cold"),
    (-5,     2, "near_zero"),
    (2,     10, "cool"),
    (10,    15, "mild"),
    (15,    25, "normal"),
    (25,   999, "hot"),
]

# Target cabin temp used for preheat reserve estimation (°C)
PREHEAT_TARGET_CABIN_TEMP = 20.0

# SOC per °C of cabin warming needed — initial estimate for Smart #1 heat pump.
# Calibrates naturally as measured preheat_soc_cost data accumulates across trips.
PREHEAT_SOC_PER_DEGREE = 0.15

# Defaults for multi-day consumption forecast.
# Override via apps.yaml: battery_kwh, typical_daily_km, forecast_horizon_days.
BATTERY_KWH           = 62.0   # Smart #1 usable battery capacity (kWh)
TYPICAL_DAILY_KM      = 30.0   # Default daily distance used when no trip distance known
FORECAST_HORIZON_DAYS = 5      # Days of forecast published to sensor.ev_consumption_forecast

# Weather forecast is reliable only within this many days.
# Days beyond this use seasonal averages, not actual forecast temperatures.
FORECAST_RELIABLE_DAYS = 3


def get_temp_band(temp):
    for lo, hi, band in TEMP_BANDS:
        if lo <= temp < hi:
            return band
    return "normal"


def get_season(month):
    return SEASON_MONTHS.get(month, "winter")


def _adjacent_bands(band, season):
    season_bands = VALID_SEASON_BANDS.get(season, [])
    idx = TEMP_BAND_ORDER.index(band) if band in TEMP_BAND_ORDER else -1
    adjacent = []
    for adj in TEMP_BAND_ORDER:
        if adj in season_bands and adj != band:
            adj_idx = TEMP_BAND_ORDER.index(adj)
            if abs(adj_idx - idx) == 1:
                adjacent.append(adj)
    return adjacent


def estimate_preheat_reserve(forecast_temp, interior_temp=None):
    """
    Estimate SOC % to reserve for unplugged cabin preheating.

    Logic: overnight the cabin cools to roughly exterior/forecast temperature.
    At trip start, preheat must warm from that expected cabin temp to PREHEAT_TARGET_CABIN_TEMP.

    If interior_temp is provided and lower than forecast_temp (car parked inside a garage etc.),
    use it as the expected cabin temp — otherwise use forecast_temp as worst-case proxy.

    Coefficient PREHEAT_SOC_PER_DEGREE is an initial estimate for Smart #1 heat pump.
    It will be refinable once preheat_soc_cost measurements accumulate in trips.json.
    """
    if forecast_temp >= 5:
        return 0  # warm enough, no preheating expected

    # Overnight the cabin equilibrates with outside air
    expected_cabin_temp = forecast_temp
    if interior_temp is not None and interior_temp < forecast_temp:
        # Car in a cold garage — cabin is colder than outside
        expected_cabin_temp = interior_temp

    temp_delta = max(0.0, PREHEAT_TARGET_CABIN_TEMP - expected_cabin_temp)
    reserve    = round(temp_delta * PREHEAT_SOC_PER_DEGREE)
    return min(10, reserve)  # cap at 10 SOC points


def lookup_profile(model_profiles, season, temp_band, drive_type, trip_type, preheating):
    preheat_key     = "preheated" if preheating else "cold_start"
    opp_preheat_key = "cold_start" if preheating else "preheated"
    exact_key       = f"{season}|{temp_band}|{drive_type}|{trip_type}|{preheat_key}"

    # 1. Exact match, ready
    p = model_profiles.get(exact_key, {})
    if p.get("ready"):
        return p, None

    # 2. Opposite preheating
    opp_key = f"{season}|{temp_band}|{drive_type}|{trip_type}|{opp_preheat_key}"
    p = model_profiles.get(opp_key, {})
    if p.get("count", 0) > 0:
        return p, f"opposite preheating ({opp_preheat_key})"

    # 3. Different drive_type, same season/band/trip/preheat
    for dt in ["mixed", "city", "highway"]:
        if dt == drive_type:
            continue
        k = f"{season}|{temp_band}|{dt}|{trip_type}|{preheat_key}"
        p = model_profiles.get(k, {})
        if p.get("count", 0) > 0:
            return p, f"adjacent drive type ({dt})"

    # 4. Adjacent temp_band
    for adj_band in _adjacent_bands(temp_band, season):
        k = f"{season}|{adj_band}|{drive_type}|{trip_type}|{preheat_key}"
        p = model_profiles.get(k, {})
        if p.get("count", 0) > 0:
            return p, f"adjacent temp band ({adj_band})"

    # 5. Season average — any drive_type, any preheating
    candidates = [v for key, v in model_profiles.items()
                  if key.startswith(f"{season}|") and v.get("count", 0) > 0]
    if candidates:
        # Use the one with most trips
        best = max(candidates, key=lambda x: x["count"])
        return best, "season average"

    return None, "no usable profile"


def compute_target(profile, fallback_reason, min_soc, buffer, typical_km=30,
                   preheat_reserve=0):
    """
    Compute target SOC.
    preheat_reserve: SOC points to add for unplugged cabin preheating
                     (estimated from forecast temperature via estimate_preheat_reserve).
    """
    if profile is None:
        return min(100, min_soc + buffer + preheat_reserve), True, fallback_reason

    km_per_soc  = profile.get("km_per_soc_ewa")
    soc_per_min = profile.get("soc_per_min_ewa")

    if km_per_soc and km_per_soc > 0:
        needed_soc = typical_km / km_per_soc
    elif soc_per_min and soc_per_min > 0:
        needed_soc = soc_per_min * 20
    else:
        return min(100, min_soc + buffer + preheat_reserve), True, "no consumption data in profile"

    target = needed_soc + min_soc + buffer + preheat_reserve
    target = max(min_soc + buffer, min(100, round(target)))
    return target, (fallback_reason is not None), fallback_reason


def build_day_estimate(temp_c, month, model_profiles, min_soc, buffer,
                       typical_km, battery_kwh, day_index=0):
    """
    Estimate SOC and kWh consumption for a single day given a forecast temperature.

    day_index: 0-based position in the forecast window (0 = tomorrow).
    forecast_reliable: True for day_index 0–2 (days 1–3), False for 3–4 (days 4–5).

    Returns a dict with soc/kwh breakdown, profile used, and reliability flag.
    """
    season     = get_season(month)
    temp_band  = get_temp_band(temp_c)
    preheating = temp_c < 5
    drive_type = "mixed"
    trip_type  = "short"

    preheat_reserve = estimate_preheat_reserve(temp_c)

    profile, fallback_reason = lookup_profile(
        model_profiles, season, temp_band, drive_type, trip_type, preheating
    )

    total_soc_pct, _, _ = compute_target(
        profile, fallback_reason, min_soc, buffer,
        typical_km=typical_km, preheat_reserve=preheat_reserve,
    )

    # driving_soc is the consumption portion excluding the safety floor
    driving_soc_pct = max(0, total_soc_pct - min_soc - buffer - preheat_reserve)

    confidence  = profile.get("confidence", "missing") if profile else "missing"
    preheat_key = "preheated" if preheating else "cold_start"
    profile_key = (
        f"{season}|{temp_band}|{drive_type}|{trip_type}|{preheat_key}"
        if profile else ""
    )

    total_kwh = round(total_soc_pct / 100.0 * battery_kwh, 2)

    return {
        "forecast_temp_c":     round(temp_c, 1),
        "season":              season,
        "temp_band":           temp_band,
        "preheating":          preheating,
        "driving_soc_pct":     driving_soc_pct,
        "preheat_reserve_pct": preheat_reserve,
        "total_soc_pct":       total_soc_pct,
        "total_kwh":           total_kwh,
        "confidence":          confidence,
        "profile":             profile_key,
        "forecast_reliable":   day_index < FORECAST_RELIABLE_DAYS,
    }


def build_forecast_block(daily_temps, month, model_profiles, min_soc, buffer,
                         typical_km, battery_kwh):
    """
    Build a multi-day forecast block from a list of (date_str, temp_float) tuples.

    Cumulative SOC and kWh are capped at 100% / battery_kwh respectively.
    Top-level forecast_reliable_days indicates how many days have reliable weather data.

    Returns:
        {
            "days": [<day_estimate dict with "date" field>, ...],
            "cumulative_soc_pct": [int, ...],   # index i = total after day i+1
            "cumulative_kwh":     [float, ...],
            "forecast_reliable_days": 3,
        }
    """
    days               = []
    cumulative_soc_pct = []
    cumulative_kwh     = []
    running_soc        = 0
    running_kwh        = 0.0

    for idx, (date_str, temp) in enumerate(daily_temps):
        day_est = build_day_estimate(
            temp, month, model_profiles, min_soc, buffer,
            typical_km, battery_kwh, day_index=idx,
        )
        day_est["date"] = date_str
        days.append(day_est)

        running_soc = min(100, running_soc + day_est["total_soc_pct"])
        running_kwh = min(battery_kwh, round(running_kwh + day_est["total_kwh"], 2))
        cumulative_soc_pct.append(running_soc)
        cumulative_kwh.append(running_kwh)

    return {
        "days":                   days,
        "cumulative_soc_pct":    cumulative_soc_pct,
        "cumulative_kwh":        cumulative_kwh,
        "forecast_reliable_days": FORECAST_RELIABLE_DAYS,
    }


class EVPredictor(hass.Hass):

    def initialize(self):
        self.log("EVPredictor starting")
        self.model_file       = self.args.get("model_file", "/config/ev_trips/consumption_model.json")
        self.min_soc          = int(self.args.get("min_soc_threshold", 20))
        self.buffer           = int(self.args.get("safety_buffer_soc", 5))
        self.battery_kwh      = float(self.args.get("battery_kwh", BATTERY_KWH))
        self.typical_daily_km = float(self.args.get("typical_daily_km", TYPICAL_DAILY_KM))
        self.horizon_days     = int(self.args.get("forecast_horizon_days", FORECAST_HORIZON_DAYS))

        self.run_hourly(self._predict)
        self.listen_state(self._on_learning_update, "sensor.ev_learning_pct")
        self.log("Predictor scheduled hourly and on learning updates")
        # Run once immediately at startup so sensors are published right away
        self._predict({})

    def _on_learning_update(self, entity, attribute, old, new, kwargs):
        self._predict({})

    def _predict(self, kwargs):
        temp       = self._get_forecast_temp()
        month      = datetime.now().month
        season     = get_season(month)
        temp_band  = get_temp_band(temp)
        preheating = temp < 5
        drive_type = "mixed"
        trip_type  = "short"

        # Preheat reserve — dynamic, based on expected cabin-to-target warming delta.
        # When temp >= 5°C this is 0. Interior temp refines the estimate (e.g. warm garage).
        interior_now    = self._get_interior_temp() if preheating else None
        preheat_reserve = estimate_preheat_reserve(temp, interior_now)

        # Load model
        if not os.path.exists(self.model_file):
            safe_target = min(100, self.min_soc + self.buffer + preheat_reserve)
            self._publish(
                target=safe_target,
                confidence="missing",
                prediction_active=False,
                status=f"No model yet. Charging to safe minimum ({safe_target}%).",
                fallback_used=True,
                fallback_reason="model file missing",
                temp=temp,
                preheating=preheating,
                preheat_reserve=preheat_reserve,
            )
            # Publish forecast with empty profiles (all days use safe minimum)
            daily_temps    = self._get_daily_temps(self.horizon_days)
            forecast_block = build_forecast_block(
                daily_temps, month, {},
                self.min_soc, self.buffer,
                self.typical_daily_km, self.battery_kwh,
            )
            self._publish_forecast(forecast_block)
            return

        try:
            with open(self.model_file) as f:
                model_data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            self.log(f"Cannot read model: {exc}", level="WARNING")
            return

        profiles     = model_data.get("profiles", {})
        learning_pct = model_data.get("learning_pct", 0)
        system_state = model_data.get("system_state", "collecting")

        profile, fallback_reason = lookup_profile(
            profiles, season, temp_band, drive_type, trip_type, preheating
        )

        target, fallback_used, fallback_reason = compute_target(
            profile, fallback_reason, self.min_soc, self.buffer,
            preheat_reserve=preheat_reserve,
        )

        confidence    = profile.get("confidence", "missing") if profile else "missing"
        trip_count    = profile.get("count", 0) if profile else 0
        preheat_label = "preheating assumed" if preheating else "no preheating"
        reserve_label = f" +{preheat_reserve}% preheat reserve" if preheat_reserve else ""

        if not fallback_used and profile:
            status = (
                f"Target SOC: {target}% (confidence: {confidence}, {trip_count} trips) | "
                f"{temp:.0f}C forecast, {preheat_label}{reserve_label}"
            )
        elif fallback_reason == "no usable profile":
            needed = max(0, 5 - trip_count)
            key    = (f"{season}|{temp_band}|{drive_type}|{trip_type}|"
                      f"{'preheated' if preheating else 'cold_start'}")
            status = (
                f"Learning ({learning_pct}% complete). "
                f"Need {needed} more trips for {key}. "
                f"Charging to safe minimum."
            )
        else:
            status = (
                f"Exact profile missing ({trip_count}/5 trips). "
                f"Using {fallback_reason}. Target SOC: {target}%"
            )

        # Truncate to 255 chars
        status = status[:255]

        self._publish(
            target=target,
            confidence=confidence,
            prediction_active=(not fallback_used),
            status=status,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            temp=temp,
            preheating=preheating,
            preheat_reserve=preheat_reserve,
        )

        # Build and publish multi-day consumption forecast
        daily_temps    = self._get_daily_temps(self.horizon_days)
        forecast_block = build_forecast_block(
            daily_temps, month, profiles,
            self.min_soc, self.buffer,
            self.typical_daily_km, self.battery_kwh,
        )
        self._publish_forecast(forecast_block)

    def _get_forecast_temp(self):
        try:
            attrs    = self.get_state(ENTITY_WEATHER, attribute="all") or {}
            forecast = attrs.get("attributes", {}).get("forecast", [])
            if forecast:
                return float(forecast[0].get("temperature", 0))
        except (TypeError, ValueError, KeyError):
            pass
        return 0.0

    def _get_interior_temp(self):
        """Current cabin temperature — used to refine preheat reserve (e.g. garage parking)."""
        try:
            v = self.get_state(ENTITY_INTERIOR_TEMP)
            return float(v) if v not in (None, "unavailable", "unknown") else None
        except (TypeError, ValueError):
            return None

    def _get_daily_temps(self, horizon_days):
        """
        Extract per-day temperatures from the weather forecast entity.
        Handles both hourly forecast entries (grouped by date, first temp taken)
        and daily entries (one per day). Falls back to repeating the last known
        temperature if fewer days are available than requested.

        Returns a list of (date_str, temp_float) tuples, length == horizon_days.
        """
        try:
            attrs    = self.get_state(ENTITY_WEATHER, attribute="all") or {}
            forecast = attrs.get("attributes", {}).get("forecast", [])
        except Exception:
            forecast = []

        daily = {}
        for entry in forecast:
            dt_str = entry.get("datetime", "")
            if not dt_str:
                continue
            try:
                date_key = dt_str[:10]          # keep "YYYY-MM-DD", drop time
                temp     = float(entry.get("temperature", 0))
                if date_key not in daily:
                    daily[date_key] = temp      # first entry per day wins
            except (TypeError, ValueError):
                continue

        result = sorted(daily.items())[:horizon_days]

        if not result:
            today_str = datetime.now().strftime("%Y-%m-%d")
            return [(today_str, 0.0)] * horizon_days

        # Pad with last known temp if forecast is shorter than horizon
        while len(result) < horizon_days:
            last_date_str, last_temp = result[-1]
            next_date = (date.fromisoformat(last_date_str) + timedelta(days=1)).isoformat()
            result.append((next_date, last_temp))

        return result

    def _publish_forecast(self, block):
        """Publish sensor.ev_consumption_forecast with multi-day breakdown attributes."""
        first_day_soc = block["days"][0]["total_soc_pct"] if block["days"] else 0
        self.set_state(
            "sensor.ev_consumption_forecast",
            state=first_day_soc,
            attributes={
                "friendly_name":          "EV Consumption Forecast",
                "unit_of_measurement":    "%",
                "battery_kwh":            self.battery_kwh,
                "typical_daily_km":       self.typical_daily_km,
                "generated_at":           datetime.now().isoformat(),
                "forecast_reliable_days": block.get("forecast_reliable_days", FORECAST_RELIABLE_DAYS),
                "days":                   block["days"],
                "cumulative_soc_pct":    block["cumulative_soc_pct"],
                "cumulative_kwh":        block["cumulative_kwh"],
            },
        )
        self.log(
            f"Published forecast: {len(block['days'])} days, "
            f"cumulative_soc={block['cumulative_soc_pct']}"
        )

    def _publish(self, target, confidence, prediction_active, status,
                 fallback_used, fallback_reason, temp, preheating, preheat_reserve):
        self.set_state(
            "sensor.ev_target_soc",
            state=target,
            attributes={
                "unit_of_measurement": "%",
                "friendly_name":       "EV Target SOC",
                "confidence":          confidence,
                "prediction_active":   prediction_active,
                "status_message":      status,
                "min_soc_threshold":   self.min_soc,
                "safety_buffer":       self.buffer,
                "fallback_used":       fallback_used,
                "fallback_reason":     fallback_reason if fallback_used else None,
                "forecast_temp":       round(temp, 1),
                "preheating_assumed":  preheating,
                "preheat_reserve_soc": preheat_reserve,
                "last_updated":        datetime.now().isoformat(),
            },
        )
        self.set_state(
            "sensor.ev_prediction_status",
            state=status[:255],
            attributes={"friendly_name": "EV Prediction Status"},
        )
        self.log(f"Published: target={target}%, preheat_reserve={preheat_reserve}%, "
                 f"fallback={fallback_used}, status={status[:80]}")
