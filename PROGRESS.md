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
**Status:** COMPLETE (pending Jussi review)
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
- [ ] Jussi has reviewed findings — proceed to Session 1

### Session 1 — Core Mode Entities
**Status:** NOT STARTED

- [ ] CLAUDE.md exists at project root
- [ ] ev_ui_sensors.yaml contains all 9 new sensors/binary sensors
- [ ] ev_ui_sensors.yaml contains all 5 new input helpers
- [ ] All new entities visible in HA Developer Tools after deployment
- [ ] sensor.ev_active_mode returns correct states
- [ ] Mode priority verified
- [ ] sensor.ev_status_narrative returns correct strings
- [ ] binary_sensor.ev_car_home uses confirmed device tracker entity
- [ ] apps.yaml entries for AppDaemon scripts

### Phase 2a — Core Entities + AppDaemon
- [ ] sensor.ev_active_mode
- [ ] sensor.ev_status_narrative
- [ ] binary_sensor.ev_car_home
- [ ] sensor.ev_last_target_decision
- [ ] sensor.ev_journey_projected_departure_soc
- [ ] sensor.ev_plan_cycle_cost
- [ ] sensor.ev_monthly_cost_summary
- [ ] sensor.ev_chart_data_updated
- [ ] sensor.ev_system_ready
- [ ] input_boolean.ev_journey_mode_enabled
- [ ] input_datetime.ev_departure_time
- [ ] input_datetime.ev_return_time
- [ ] input_boolean.ev_preheat_from_grid
- [ ] input_number.ev_grid_fixed_fee_ckwh
- [ ] utils.py (shared utility module)
- [ ] mode_resolver.py
- [ ] status_narrator.py
- [ ] chart_data_writer.py
- [ ] startup_monitor.py

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
| Nordpool entity name | `sensor.nordpool_kwh_fi_eur_4_095_0255` |
| Nordpool attribute structure | See detailed section below |
| Existing AppDaemon write pattern | `self.set_state()` — no retry, no error handling. See details below |
| Session cost boundary detection | `input_boolean.ev_charge_session_active` + automations. See details below |
| Slot ranking mechanism summary | Price-ascending sort in `ev_price_slots_15m_effective`. See details below |
| Git baseline tag confirmed | `pre-ev-dashboard-baseline` at commit `be6381e` |
| ev_session_cost_v3.yaml location | Nested: `/config/packages/packages/ev_session_cost_v3.yaml` on HA |

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

### Nordpool Attribute Structure

- `today`: list of 96 floats (15-min resolution, EUR/kWh)
- `tomorrow`: list of 96 floats (available after ~14:00 CET)
- `raw_today`: list of 96 objects `{start: ISO, end: ISO, value: float}`
- `raw_tomorrow`: list of 96 objects `{start: ISO, end: ISO, value: float}`
- `current_price`: float (current 15-min slot price)
- `tomorrow_valid`: boolean (whether tomorrow prices are published)
- Other: average, min, max, mean, peak, off_peak_1, off_peak_2, low_price, price_percent_to_average
- `price_in_cents`: boolean flag
- `unit`: "kWh", `currency`: "EUR", `country`: "Finland", `region`: "FI"

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

1. **AppDaemon scripts are flat** in `/addon_configs/a0d7b954_appdaemon/apps/` — NOT in a `smart-ev-learning/` subdirectory. New scripts will need to be deployed to the flat directory unless we create the subdirectory on HA. The local mirror uses the subdirectory convention.
2. **ev_session_cost_v3.yaml is nested** in `/config/packages/packages/` — double-nested packages directory on HA.
3. **Smart #1 preheating sensor EXISTS** — `sensor.smart_pre_climate_active`. This means `preheat_compensator.py` can be implemented (not deferred).
4. **Nordpool is 15-minute resolution** (96 entries/day), not hourly. The existing `ev_prices_effective_and_plan.yaml` already handles this.
5. **Second device_tracker** `device_tracker.smartvehicle871` exists (iBeacon-based) alongside the GPS-based `device_tracker.smart_none`. Use `smart_none` for `ev_car_home` (it has GPS coords).

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
