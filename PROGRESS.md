# EV Smart Charging — Progress Log

**Source of truth for project state. Updated at start and end of every session.**

| Property | Value |
| --- | --- |
| AppDaemon Path | `/addon_configs/a0d7b954_appdaemon/apps/smart-ev-learning/` |
| Git Baseline Tag | _(pending Session 0)_ |

---

## Session Progress

### Session 0 — System Baseline and Verification
**Status:** IN PROGRESS
**Date started:** 2026-03-03

- [ ] Local mirror current — all 6 YAML packages pulled from HA
- [ ] AppDaemon scripts pulled from HA
- [ ] Git baseline committed and tagged `pre-ev-dashboard-baseline`
- [ ] AppDaemon apps path verified
- [ ] All 17 existing entities from DESIGN_SPEC Section 12.4 verified
- [ ] Smart #1 device tracker entity name confirmed
- [ ] Smart #1 preheating sensor status confirmed
- [ ] Smart #1 battery capacity and max AC charge rate confirmed
- [ ] Nordpool entity attribute structure documented
- [ ] Existing AppDaemon write pattern documented
- [ ] Session cost boundary detection documented
- [ ] Slot ranking mechanism documented
- [ ] `ha core check` passes
- [ ] All 3 existing AppDaemon scripts confirmed running
- [ ] PROGRESS.md decisions log complete
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
| AppDaemon path verified | _(pending)_ |
| Smart #1 device tracker entity | _(pending)_ |
| Smart #1 preheating sensor | _(pending)_ |
| Smart #1 battery capacity (kWh) | _(pending)_ |
| Smart #1 max AC charge rate (kW) | _(pending)_ |
| Nordpool entity name | `sensor.nordpool_kwh_fi_eur_4_095_0255` |
| Nordpool attribute structure | _(pending)_ |
| Existing AppDaemon write pattern | _(pending)_ |
| Session cost boundary detection | _(pending)_ |
| Slot ranking mechanism summary | _(pending)_ |
| Git baseline tag confirmed | _(pending)_ |

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
