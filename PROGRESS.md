# EV Smart Charging — Progress Log

**Source of truth for project state. Updated at start and end of every session.**

| Property | Value |
| --- | --- |
| AppDaemon Path | `/addon_configs/a0d7b954_appdaemon/apps/` (flat, no `smart-ev-learning` subdirectory on HA) |
| Local Mirror Path | `appdaemon/apps/smart-ev-learning/` (uses subdirectory convention locally) |
| Git Baseline Tag | `pre-ev-dashboard-baseline` (commit `be6381e`, 2026-03-03) |
| SSH Access | `ssh -i ~/.ssh/homeassistant root@homeassistant.local` |

---

## Session Progress

### Session 0 — System Baseline and Verification
**Status:** COMPLETE
**Date started:** 2026-03-03

- [x] Local mirror current — all 6 YAML packages verified identical to HA (CRLF/LF only)
- [x] AppDaemon scripts pulled from HA: predictor.py, model_updater.py, ev_trip_logger.py, apps.yaml
- [x] Git baseline committed and tagged `pre-ev-dashboard-baseline`
- [x] AppDaemon apps path verified: `/addon_configs/a0d7b954_appdaemon/apps/`
- [x] All 17 existing entities from DESIGN_SPEC Section 12.4 verified (see entity table below)
- [x] Smart #1 device tracker entity name confirmed: `device_tracker.smart_none`
- [x] Smart #1 preheating sensor status confirmed: EXISTS — `sensor.smart_pre_climate_active`
- [x] Smart #1 battery capacity and max AC charge rate confirmed: 62 kWh / ~11 kW
- [x] Nordpool entity attribute structure documented (see below)
- [x] Existing AppDaemon write pattern documented (see below)
- [x] Session cost boundary detection documented (see below)
- [x] Slot ranking mechanism documented (see below)
- [x] `ha core check` passes
- [x] All 3 existing AppDaemon scripts confirmed running (+ heater_forecast)
- [x] PROGRESS.md decisions log complete
- [x] Jussi has reviewed findings — proceed to Session 1

### Session 1 — Core Mode Entities
**Status:** COMPLETE
**Date started:** 2026-03-04
**Date completed:** 2026-03-04

- [x] CLAUDE.md exists at project root
- [x] ev_ui_sensors.yaml contains all 9 new sensors/binary sensors
- [x] ev_ui_sensors.yaml contains all 5 new input helpers
- [x] All new entities visible in HA Developer Tools after deployment
- [x] sensor.ev_active_mode returns correct states
- [x] Mode priority verified: disabled ✓, charge_now ✓, smart_waiting ✓ (live = smart_waiting)
- [x] sensor.ev_status_narrative returns correct strings (live = "Waiting for cheap slot. On track for 23:30.")
- [x] binary_sensor.ev_car_home uses confirmed device tracker entity (device_tracker.smart_none, live = on)
- [ ] apps.yaml entries for AppDaemon scripts (Session 2 task)

### Session 2 — AppDaemon Scripts
**Status:** DEPLOYED — pending chart_data.json verification
**Date started:** 2026-03-05
**Date completed:** 2026-03-06

- [x] utils.py — ha_write() with 3-retry, 2s backoff, health flag on failure
- [x] mode_resolver.py — full Section 2.2 priority logic, 13 modes, writes input_text.ev_active_mode
- [x] status_narrator.py — full Section 5.1 narrative table (19 strings), writes input_text.ev_status_narrative
- [x] chart_data_writer.py — builds chart_data.json from price curve + planned slots, atomic write
- [x] startup_monitor.py — checks 6 required apps loaded, writes input_text.ev_system_ready
- [x] predictor.py — additive: writes input_text.ev_last_target_decision with decision attributes
- [x] ev_ui_sensors.yaml — 5 template sensors converted to input_text pass-throughs; 5 input_text entities added
- [x] apps.yaml — 4 new AppDaemon app entries added
- [x] All scripts deployed to /addon_configs/a0d7b954_appdaemon/apps/
- [x] ha core check passes
- [x] All 4 new AppDaemon scripts confirmed running (thread pinning in logs: threads 4–7)
- [x] /config/www/ev-dashboard/ directory created on HA
- [ ] chart_data.json confirmed written (pending — directory was missing initially, manually created; verify next cycle)
- [ ] Entity state spot-check: sensor.ev_active_mode, sensor.ev_status_narrative, sensor.ev_system_ready

### Phase 2a — Core Entities + AppDaemon
- [x] sensor.ev_active_mode (Session 1 template — AppDaemon pass-through in Session 2)
- [x] sensor.ev_status_narrative (Session 1 template — AppDaemon pass-through in Session 2)
- [x] binary_sensor.ev_car_home
- [x] sensor.ev_last_target_decision
- [x] sensor.ev_journey_projected_departure_soc
- [x] sensor.ev_plan_cycle_cost
- [x] sensor.ev_monthly_cost_summary
- [x] sensor.ev_chart_data_updated
- [x] sensor.ev_system_ready
- [x] input_boolean.ev_journey_mode_enabled
- [x] input_datetime.ev_departure_time
- [x] input_datetime.ev_return_time
- [x] input_boolean.ev_preheat_from_grid
- [x] input_number.ev_grid_fixed_fee_ckwh
- [x] utils.py (shared utility module)
- [x] mode_resolver.py
- [x] status_narrator.py
- [x] chart_data_writer.py
- [x] startup_monitor.py

---

## Decisions Log

### Session 0 Findings

| Field | Value |
| --- | --- |
| AppDaemon path verified | YES — `/addon_configs/a0d7b954_appdaemon/apps/` (flat, no subdirectory) |
| Smart #1 device tracker entity | `device_tracker.smart_none` (state=home, has lat/long/gps_accuracy) |
| Smart #1 preheating sensor | EXISTS — `sensor.smart_pre_climate_active` (state=False) |
| Smart #1 battery capacity (kWh) | **62.0 kWh** (from apps.yaml `battery_kwh: 62.0`) |
| Smart #1 max AC charge rate (kW) | **~11 kW** (Zaptec max 16A × 3 phases × 230V = 11.04 kW) |
| Nordpool raw spot entity | `sensor.nordpool_kwh_fi_eur_4_095_0255` (spot only, today+tomorrow) |
| Nordpool Predict FI entity | `sensor.nordpool_predict_fi_price` (forecast, 194 hourly entries, ~8 days) |
| Combined price sensor | `sensor.ev_price_slots_15m_effective` (merged spot+predict, 776 15-min slots, ~8 days) |
| Price data for chart_data_writer | Use `sensor.ev_price_slots_15m_effective` — already merged and ranked |
| Planning horizon | Spot: ~2 days, With predict: ~6-8 days (`sensor.ev_pricing_horizon_end_ts`) |
| Nordpool attribute structure | See detailed section below |
| Existing AppDaemon write pattern | `self.set_state()` — no retry, no error handling. See details below |
| Session cost boundary detection | `input_boolean.ev_charge_session_active` + automations. See details below |
| Slot ranking mechanism summary | Price-ascending sort in `ev_price_slots_15m_effective`. See details below |
| Git baseline tag confirmed | `pre-ev-dashboard-baseline` at commit `be6381e` |
| ev_session_cost_v3.yaml location | `/config/packages/ev_session_cost_v3.yaml` (moved from nested packages/packages/ during Session 0) |

### Session 2 Decisions

| Field | Value |
| --- | --- |
| sensor.ev_price_slots_15m_effective attribute | `data` — confirmed correct (entries: {DateTime, ts, PriceWithTax, Rank}) |
| chart_data.json write failure cause | Directory /config/www/ev-dashboard/ didn't exist on first run; os.makedirs in initialize() created it, but file wasn't observed until after manual dir creation. Attribute name was NOT the issue. |
| input_text backend pattern | AppDaemon writes to input_text.*, template sensor reads from it — avoids HA template re-evaluation conflict with AppDaemon set_state() |
| Tier 3 journey warning | Fires once per departure (_tier3_fired flag), checks hours_to_dep * 11 kW < (100-soc)/100 * 62 kWh |
| Bargain mode detection | Reads sensor.ev_price_slots_15m_effective.data, finds current 15-min slot by (now // 900) * 900, compares PriceWithTax * 100 ≤ input_number.ev_bargain_price_threshold |

### Session 1 Decisions

| Field | Value |
| --- | --- |
| ev_car_home device tracker | `device_tracker.smart_none` — GPS tracker with lat/long/gps_accuracy confirmed Session 0. NOT `device_tracker.smartvehicle871` (iBeacon). |
| ev_active_mode warning condition | Using `binary_sensor.ev_forecast_reliability_warning` as P1 warning trigger for Session 1 template. Full warning logic (window critically short, shortfall unrecoverable) implemented by mode_resolver.py in Session 2. |
| not_plugged detection | `sensor.zag063912_charger_mode in ['disconnected','unknown']` — confirmed from ev_automations.yaml. |
| ev_status_narrative strings | 6 of 19 Section 5.1 strings implemented. Full table implemented by status_narrator.py in Session 2. |
| ev_system_ready default | String "false" (not boolean) — HA template sensor states are strings. AppDaemon writes "true" string. |

### Verified Entity States (2026-03-03)

| Entity | State | Type/Notes |
| --- | --- | --- |
| sensor.smart_battery | 32 | Integer 0–100, % |
| sensor.smart_motor | engine_off | String engine state |
| sensor.nordpool_kwh_fi_eur_4_095_0255 | 0.0714 | EUR/kWh numeric |
| sensor.ev_consumption_forecast | 39 | % (first day forecast SOC) |
| sensor.ev_plan_15m_rank_effective | ok | Slot ranking active |
| sensor.ev_learning_forecast_soc | 78 | % |
| sensor.ev_learning_min_window_price | 0.3 | c/kWh |
| binary_sensor.ev_forecast_reliability_warning | off | Boolean |
| binary_sensor.ev_should_charge_now_combined | off | Boolean |
| input_datetime.ev_deadline | 2026-03-04 23:30:00 | Datetime |
| input_number.ev_target_kwh | 0.5 | kWh |
| input_number.ev_max_soc | 100.0 | % |
| input_number.ev_min_soc_pct | 30.0 | % |
| input_number.ev_min_charge_by_hour | 8.0 | Hour (morning deadline) |
| input_number.ev_bargain_price_threshold | 5.5 | c/kWh |
| input_boolean.ev_smart_charge_enabled | on | Master enable |
| input_boolean.ev_min_charge_enabled | on | Min charge guard |
| input_boolean.ev_charge_now_override | off | Manual override |

### Price Data Architecture

**Three-layer price pipeline:**

1. **Raw Nordpool spot** (`sensor.nordpool_kwh_fi_eur_4_095_0255`): today+tomorrow only, 96 entries/day (15-min), EUR/kWh
2. **Nordpool Predict FI** (`sensor.nordpool_predict_fi_price`): multi-day forecast, 194 hourly entries (~8 days), `forecast` attribute
3. **Combined effective** (`sensor.ev_price_slots_15m_effective`): merged spot+predict, 776 15-min slots (~8 days), ranked by price
   - Each entry: `{DateTime: ISO, Rank: int, PriceWithTax: float(EUR/kWh)}`
   - Spot data takes priority where available; predict extends beyond spot horizon

**For chart_data_writer.py:** Use `sensor.ev_price_slots_15m_effective` directly — it's already merged, ranked, and in 15-min resolution.

**Horizon sensors:**
- `sensor.ev_spot_horizon_end_ts` — end of confirmed spot data
- `sensor.ev_pricing_horizon_end_ts` — end of combined spot+predict data (reliability cutoff)
- `sensor.ev_planning_horizon_end_ts` — effective planning horizon (spot-only or combined, controlled by `input_boolean.ev_allow_predicted_hours`)

**Raw Nordpool spot attributes** (for reference only — chart_data_writer uses the combined sensor):
- `today`/`tomorrow`: lists of 96 floats (EUR/kWh)
- `raw_today`/`raw_tomorrow`: lists of 96 objects `{start: ISO, end: ISO, value: float}`
- `current_price`, `tomorrow_valid`, `price_in_cents`, `unit`: "kWh", `currency`: "EUR"

### AppDaemon Write Pattern

All existing scripts use `self.set_state(entity_id, state=value, attributes={...})` exclusively:
- **No retry logic** — writes are fire-and-forget
- **No error handling** around set_state calls
- Supports complex nested dicts/lists in attributes (auto-serialized to JSON)
- `utils.py ha_write()` should wrap `self.set_state()` and ADD retry (3 attempts, 2s backoff) + logging + health flag

### Session Cost Boundary Detection

- Session boundaries detected by `input_boolean.ev_charge_session_active` (explicit state, not heuristic)
- `binary_sensor.ev3_session_active` mirrors this boolean
- On start: captures baseline kWh from `sensor.zag063912_session_total_charge`, resets cost accumulator
- On end: records end timestamp
- Accumulation: triggered by changes to `sensor.zag063912_session_total_charge`, calculates delta × price
- Key entities: `input_number.ev3_session_cost_actual_eur`, `sensor.ev3_session_delivered_kwh`, `sensor.ev3_estimated_cost_eur`
- `session_monitor.py` should aggregate on top of these — NOT duplicate

### Slot Ranking Mechanism

- Slots ranked by `PriceWithTax` ascending in `sensor.ev_price_slots_15m_effective`
- Active slot list: `sensor.ev_plan_15m_rank_effective` attribute `planned_slots[]` — array of `{ts, rank}`
- Selection: future slots within deadline, sorted by rank, limited to N slots needed for target kWh
- Recalculation: event-driven (state changes) + time-pattern (every 5 min reconcile, every 15 min quarter tick)
- Charging control: `binary_sensor.ev_should_charge_now_15m_effective` checks if current quarter-ts is in planned_slots
- Master automation: `ev_control_effective_master` activates/deactivates charger via `button.zag063912_resume_charging` / `button.zag063912_stop_charging`

### Surprises / Deviations from Plan

1. **AppDaemon scripts are flat** in `/addon_configs/a0d7b954_appdaemon/apps/` — no `smart-ev-learning/` subdirectory on HA. Deploy new scripts to flat directory. Local mirror uses subdirectory convention for organization.
2. **ev_session_cost_v3.yaml** was at `/config/packages/packages/` — FIXED: moved to `/config/packages/` during Session 0. `ha core check` passes after move.
3. **Smart #1 preheating sensor EXISTS** — `sensor.smart_pre_climate_active`. `preheat_compensator.py` can be implemented (not deferred).
4. **Two device_trackers** for Smart #1: `device_tracker.smart_none` (GPS with lat/long) and `device_tracker.smartvehicle871` (iBeacon). Using `device_tracker.smart_none` for `ev_car_home` per spec (GPS, never phone location).

---

## Feature Flags Status

| Flag | Status | Enabled after |
| --- | --- | --- |
| journeyMode | false | Session 7 |
| inTransitProjection | false | Session 7 |
| costTracking | false | Session 3 |
| shortfallAlerts | false | Session 3 |
| gridFees | false | When entity created |
| calendarTrigger | false | Session 9 |
| mockMode | false | Never in production |
