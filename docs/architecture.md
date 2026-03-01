# EV Smart Charging — Technical Architecture

## Data Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PRICE DATA PIPELINE                                                    │
│                                                                         │
│  sensor.nordpool_predict_fi_price  ──► ev_predict_hourly_series         │
│    (hourly c/kWh, 12 h ahead)           (normalised EUR/kWh)           │
│                                              │                          │
│                                         ev_predict_15m_series           │
│                                           (expanded to 15 min,          │
│                                            globally ranked)             │
│                                              │                          │
│  sensor.ev_price_slots_15m (spot) ──────────┤                          │
│                                              ▼                          │
│                                   ev_price_slots_15m_effective          │
│                                   (merged spot + predict,               │
│                                    globally ranked, DateTime + ts)      │
└─────────────────────────────────────────────────────────────────────────┘
                                              │
                    ┌─────────────────────────┴──────────────────────┐
                    ▼                                                 ▼
        ┌─────────────────────┐                          ┌───────────────────────┐
        │  SMART PLAN          │                          │  MIN-CHARGE PLAN      │
        │                      │                          │                       │
        │  ev_plan_15m_rank    │                          │  ev_min_charge_plan   │
        │  (deadline-bounded,  │                          │  (daily deadline,     │
        │   cheapest N slots)  │                          │   min SOC target)     │
        │         │            │                          │         │             │
        │  ev_plan_15m_rank    │                          │  ev_should_charge     │
        │    _effective        │                          │    _now_min_charge    │
        │  (spot OR predict    │                          │                       │
        │   based on toggle)   │                          └───────────────────────┘
        │         │            │                                      │
        └─────────┼────────────┘                                      │
                  │                                                    │
        ev_should_charge_now_15m_effective                            │
                  │                                                    │
                  └──────────────────────┬────────────────────────────┘
                                         │
                               ev_charge_now_override ──┐
                                         │               │
                                         ▼               ▼
                                ev_should_charge_now_combined
                                         │
                                         ▼
                              ev_control_effective_master
                              (restart-mode automation)
                                         │
                              ┌──────────┴──────────┐
                              ▼                     ▼
                    button.zag063912         switch.zag063912
                    _stop_charging           _charging (resume)
```

---

## Package Files

### `ev_prices_effective_and_plan.yaml`

**Sensors:**

| Sensor | State | Key Attributes |
|--------|-------|----------------|
| `ev_predict_horizon_end_ts` | Unix ts | `horizon_end_local`, `horizon_hours` (always 12) |
| `ev_predict_15m_series` | `ok` | `data[]` — `{DateTime, ts, PriceWithTax, Rank}` in EUR/kWh |
| `ev_plan_15m_rank` | `ok`/`no-data` | `q` (slots needed), `preview_slots[]`, `planned_slots[]` — slots: `{ts, rank, price}` |

**Binary sensors:**

| Sensor | Trigger |
|--------|---------|
| `ev_should_charge_now_15m` | Current 15-min slot is in `planned_slots` |

**Automations:**

| ID | Trigger | Action |
|----|---------|--------|
| `ev_deadline_guard_clamp_to_predict_horizon` | `ev_deadline` or `ev_predict_horizon_end_ts` changes | Clamps deadline to `[now+15m … horizon]` |

**Slot buffer logic in `ev_plan_15m_rank`:**
- `q_base` = `ceil(rem_kwh / per_q_kwh)`
- At 75 % SOC progress: `q_buf = 1`; at 90 %: `q_buf = 2`
- If deadline is tight (`remaining_quarters ≤ q_base + 1`): `q = q_base + 2`

---

### `ev_effective_predict_and_control.yaml`

**Horizon sensors:**

| Sensor | Description |
|--------|-------------|
| `ev_spot_horizon_end_ts` | Last timestamp in spot price series |
| `ev_pricing_horizon_end_ts` | Last timestamp in effective (merged) series |
| `ev_planning_horizon_end_ts` | `ev_pricing_horizon_end_ts` if allow_predicted, else `ev_spot_horizon_end_ts` |
| `ev_predict_source_status` | `ok` / `unavailable` — whether Nordpool Predict is reachable |

**Price pipeline sensors:**

| Sensor | Description |
|--------|-------------|
| `ev_predict_hourly_series` | Parses `nordpool_predict_fi_price.forecast` — converts c/kWh → EUR/kWh, expands to `{ts, DateTime, PriceWithTax}` |
| `ev_price_slots_15m_effective` | Merges spot + predicted, globally re-ranks all slots. Attribute `data[]` has `{DateTime, ts, PriceWithTax, Rank, PriceCt}` |

**Effective plan sensor:**

`ev_plan_15m_rank_effective` — mirrors `ev_plan_15m_rank` logic but uses `ev_price_slots_15m_effective` as source. `planned_slots` attribute used by the master control automation.

**Charge Now sensors:**

| Entity | Description |
|--------|-------------|
| `input_boolean.ev_charge_now_override` | Toggle — auto-cleared on target reached or disable |

**Min-charge sensors:**

| Sensor | Formula / Logic |
|--------|-----------------|
| `sensor.ev_min_charge_target_kwh` | `max(0, (min(min_soc_pct, max_soc) - soc_now) / 100 × 62.0 kWh)` |
| `sensor.ev_min_charge_deadline_ts` | `today_at(min_charge_by_hour)` if > 5 min away, else `tomorrow_at(...)` |
| `sensor.ev_min_charge_plan` | Cheapest N slots within `[now … min(min_deadline, planning_horizon)]`. Slots: `{ts, rank}` (no price embedded — look up from `ev_price_slots_15m_effective`) |

**Binary sensors:**

| Sensor | Logic |
|--------|-------|
| `ev_should_charge_now_min_charge` | Current slot in `ev_min_charge_plan.planned_slots` |
| `ev_should_charge_now_combined` | `charge_now OR (smart_should AND remaining>0) OR min_should` — gated on `ev_smart_charge_enabled` |
| `ev_warning_not_enough_slots` | `available_slots × per_q_kwh < ev_remaining_used_kwh` |
| `ev_warning_min_charge_not_enough_slots` | `available_min_slots × per_q_kwh < ev_min_charge_target_kwh` |
| `ev_warning_confirmed_only_deadline_beyond_spot` | Deadline is beyond spot horizon when predicted hours are disabled |

**Input helpers:**

| Entity | Type | Default | Range |
|--------|------|---------|-------|
| `ev_smart_charge_enabled` | boolean | — | — |
| `ev_allow_predicted_hours` | boolean | on | — |
| `ev_charge_now_override` | boolean | off | — |
| `ev_min_charge_enabled` | boolean | off | — |
| `ev_min_soc_pct` | number | 80 % | 20–100, step 5 |
| `ev_min_charge_by_hour` | number | 8 | 0–23, step 1 |

**Automations:**

| ID | Mode | Trigger | Purpose |
|----|------|---------|---------|
| `ev_control_effective_master` | restart | 15-min time pattern + state changes on all relevant entities | Main control loop — evaluates `should_effective`, resumes or stops charger |
| `ev_charge_now_auto_off` | single | `ev_remaining_used_kwh < 0.05` for 30 s | Clears `ev_charge_now_override` |
| `ev_deadline_guard_clamp_to_pricing_horizon` | single | Deadline or horizon change | Keeps deadline within reachable horizon |
| `ev_session_cost_freeze_on_smart_disable` | single | Smart disabled | Snapshots cost to `ev_session_cost_frozen_eur` |
| `ev_session_cost_log_on_session_end` | single | Session ends | Logs final cost, updates `ev_session_cost_last_eur` and `ev_session_last_end` |

**Master automation logic (simplified):**

```yaml
variables:
  enabled:        ev_smart_charge_enabled = on
  remaining:      sensor.ev_remaining_used_kwh | float
  charge_now:     ev_charge_now_override = on  AND  remaining > 0
  should:         ev_should_charge_now_15m_effective = on  AND  remaining > 0
  min_should:     ev_min_charge_enabled = on  AND  ev_should_charge_now_min_charge = on
  should_effective: charge_now OR should OR min_should
  charging_active: switch.zag063912_charging = on  OR  charger_mode = connected_charging

choose:
  - NOT enabled  →  stop if charging; turn off ev_charge_now_override
  - enabled AND should_effective AND NOT charging_active  →  button.press(resume_charging)
  default: enabled AND NOT should_effective AND charging_active  →  button.press(stop_charging)
```

---

### `ev_session_cost_v3.yaml`  *(config/packages/packages/)*

**Sensors:**

| Sensor | Description |
|--------|-------------|
| `ev3_session_delivered_kwh` | `sensor.zag063912_session_total_charge − ev3_session_start_kwh` |
| `ev3_price_now_eur_kwh_allin` | Current 15-min slot price + `ev_transfer_fee_eur_kwh` |
| `ev3_estimated_cost_eur` | `ev3_session_cost_actual_eur` + projected remaining (planned slots × price) |
| `ev_planned_cost_eur_v3` | Wrapper with rich attributes: `actual_so_far_eur`, `batt_need_kwh`, `efficiency`, `planned_energy_kwh`, `slots_count`, `base_energy_cost_eur` |

**Input helpers:**

| Entity | Default | Description |
|--------|---------|-------------|
| `ev_transfer_fee_eur_kwh` | 0.06387 | Grid transfer fee (sähkön siirtomaksu) |
| `ev3_charge_efficiency` | 1.0 | AC→battery efficiency factor (0.80–1.00) |
| `ev3_session_start_kwh` | — | Energy meter reading at session start |
| `ev3_session_cost_actual_eur` | — | Running cost accumulator (zeroed at session start) |
| `ev3_last_energy_total_kwh` | — | Previous meter reading for delta calculation |

**Automations:**

| ID | Trigger | Action |
|----|---------|--------|
| `ev3_session_start_capture` | `ev3_session_active`: off→on | Record `ev3_session_start_kwh` + `ev3_session_start` datetime |
| `ev3_session_end_timestamp` | `ev3_session_active`: on→off | Record `ev3_session_end` datetime |
| `ev3_session_cost_reset` | `ev3_session_active`: off→on | Zero `ev3_session_cost_actual_eur` and `ev3_last_energy_total_kwh` |
| `ev3_session_cost_accumulate` | `zag063912_session_total_charge` changes | `new_cost = current_cost + delta_kwh × price_now`; update both accumulators |

---

### `automations.yaml` — EV automations

| ID | Purpose |
|----|---------|
| `ev_price_slots_15m_refresh` | Periodic refresh trigger for price slot sensor |
| `ev_enabled_capture_start_kwh` | Captures energy meter when smart charging is enabled |
| `ev_enabled_capture_start_soc` | Captures SOC when smart charging is enabled (for progress tracking) |
| `ev_target_kwh_clamp_to_soc_limit` | Clamps target kWh to what the SOC cap allows |
| `ev_deadline_guard_no_past` | Prevents deadline being set in the past |
| `ev_campaign_cost_reset_on_session_start` | Resets 15-min campaign cost accumulator at session start |
| `ev_campaign_cost_accumulate_15m` | 15-min cost accumulation (legacy, feeds session history) |
| `ev_plug_in_takeover_gate` | When cable is plugged in: checks `ev_should_charge_now_combined` and starts charging if appropriate |
| `ev_takeover_armed_init_on_start` | Initialises plug-in gate state on HA start |
| `ev_takeover_rearm_on_disconnect_or_safe_unknown` | Re-arms plug-in gate when cable is removed |
| `ev_target_reached_stop` | Stops charger when `ev_remaining_used_kwh < 0.05` for 30 s, unless min-charge is active |

---

### `configuration.yaml` — `sensor.ev_schedule_check`

A diagnostic sensor that produces a human-readable status string for the current charging state.

**State format:**
```
DISABLED
SMART | SOC 94% | rem 3.0kWh need 2 left 382x15m ok
CHARGE_NOW | SOC 72% | rem 2.5kWh need 10 left 24x15m ok
SMART+MIN | SOC 60% | rem 5.0kWh need 20 left 48x15m ok | min 1.5kWh need 6 left 10x15m TIGHT!
```

**Attributes:** `mode`, `charge_now_active`, `min_charge_active`, `soc_now`, `soc_target`, `deadline_local`, `smart_rem_kwh`, `smart_need_q`, `smart_left_q`, `smart_tight`, `min_charge_target_kwh`, `min_charge_deadline_local`, `min_charge_left_q`, `min_charge_tight`, `effective_kw`

---

## Session Lifecycle

```
Cable plugged in
  └─► ev_plug_in_takeover_gate fires
        └─► if ev_should_charge_now_combined = on → resume_charging

ev_smart_charge_enabled turned on
  └─► capture start SOC + kWh
  └─► ev3_session_active = on → ev3_session_start_capture fires (log baseline)

Every 15 minutes: ev_control_effective_master evaluates should_effective
  → resume or stop charger

ev_remaining_used_kwh < 0.05 for 30 s
  └─► ev_target_reached_stop → stop charger
  └─► ev_charge_now_auto_off → clear override

Cable unplugged
  └─► ev3_session_active = off → ev3_session_end_timestamp fires
  └─► ev_session_cost_log_on_session_end → saves cost + datetime to history
```

---

## Configuration Notes

- **Battery capacity**: hardcoded `62.0 kWh` in min-charge formula. Update if car changes.
- **ApexCharts display fee**: `6.7 c/kWh` hardcoded in JavaScript data_generators (display only; does not affect control logic). The actual fee used in control is `input_number.ev_transfer_fee_eur_kwh`.
- **Nested package location**: `ev_session_cost_v3.yaml` is in `packages/packages/` — ensure `configuration.yaml` uses `!include_dir_merge_named` (not `!include_dir_named`) so nested directories are scanned.
- **Planning horizon**: always ≤ 12 h from now (limited by predict integration). The `ev_planning_horizon_end_ts` sensor is the authoritative upper bound.
