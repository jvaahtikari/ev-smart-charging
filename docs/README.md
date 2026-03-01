# EV Smart Charging System

A fully automated EV charging scheduler for Home Assistant that optimises charging cost using
real-time spot prices and multi-day Nordpool price forecasts. Built for a **Smart #1 car** and
**Zaptec Pro charger**, but the logic is adaptable to any EV + charger combination.

---

## Features

| Feature | Description |
|---------|-------------|
| **Smart scheduling** | Picks the cheapest 15-minute slots within a user-set deadline |
| **Multi-day price forecast** | Extends planning beyond spot-price availability using Nordpool Predict FI (typically 4–8 days of hourly prices) |
| **Charge Now override** | Charge immediately regardless of price; auto-disables when target is reached |
| **Minimum charge level** | Guarantees a minimum SOC by a configurable daily hour (e.g. 08:00) |
| **Session cost tracking** | Real-time cost accumulation; per-session history with 7d/30d statistics |
| **Lovelace dashboard** | Full-featured dashboard with price chart, slot visualisation, cost history |
| **Warnings** | Alerts when not enough cheap slots exist to reach either target by deadline |

---

## Hardware

| Component | Entity prefix | Notes |
|-----------|--------------|-------|
| Smart #1 | `sensor.smart_*` | SOC via Smart integration |
| Zaptec Pro charger | `switch.zag063912_*`, `button.zag063912_*`, `sensor.zag063912_*` | Zaptec integration |
| Nordpool Predict FI | `sensor.nordpool_predict_fi_*` | Custom integration — provides hourly price forecasts (often 4–8 days ahead) |

---

## Operating Modes

Modes are evaluated in strict priority order:

```
1. DISABLED     ev_smart_charge_enabled = off
                → stops charger, clears Charge Now override

2. CHARGE NOW   ev_charge_now_override = on  AND  remaining > 0
                → charges immediately at full power until target kWh delivered
                → auto-disables when ev_remaining_used_kwh < 0.05 kWh (30 s debounce)

3. MIN CHARGE   ev_min_charge_enabled = on  AND  current slot in min-charge plan
                → picks cheapest slots within [now … next occurrence of ev_min_charge_by_hour]
                → uses own SOC target (ev_min_soc_pct), capped at ev_max_soc
                → never modifies the long-term smart plan

4. SMART        current slot in ev_plan_15m_rank_effective.planned_slots  AND  remaining > 0
                → normal cheapest-slot scheduling within the deadline window

5. IDLE         none of the above → charger stopped
```

`should_effective = charge_now OR (smart_should AND remaining > 0) OR min_should`

---

## File Map

```
packages/
  ev_effective_predict_and_control.yaml   ← master control + Charge Now + Min Charge + horizon sensors
  ev_prices_effective_and_plan.yaml       ← price data pipeline + slot planner
  ev_ui_sensors.yaml                      ← UI/diagnostic template sensors & helper numbers
  ev_automations.yaml                     ← EV automations (guards, takeover, session tracking)
  ev_session_cost_v3.yaml                 ← session cost accumulator + statistics

lovelace/
  ev_charging_dashboard.json             ← Lovelace raw config (paste into HA UI)

docs/
  README.md          ← this file
  architecture.md    ← full technical reference (all entities, data flow, automations)
  dashboard.md       ← Lovelace dashboard guide and card documentation
```

---

## Quick Entity Reference

### User-facing inputs

| Entity | Default | Purpose |
|--------|---------|---------|
| `input_boolean.ev_smart_charge_enabled` | — | Master on/off |
| `input_boolean.ev_allow_predicted_hours` | on | Include Nordpool Predict FI forecast slots in planning |
| `input_boolean.ev_charge_now_override` | off | Charge Now mode |
| `input_boolean.ev_min_charge_enabled` | off | Minimum charge level mode |
| `input_datetime.ev_deadline` | — | Smart plan deadline (can be days ahead when `ev_allow_predicted_hours = on`) |
| `number.ev_target_energy_kwh_ui` | — | Energy target for smart plan (kWh above current) |
| `number.ev_max_soc_ui` → `input_number.ev_max_soc` | 100 % | SOC cap |
| `input_number.ev_min_soc_pct` | 80 % | Minimum SOC target for min-charge mode |
| `input_number.ev_min_charge_by_hour` | 8 | Daily hour (0–23) for min-charge deadline (next occurrence of this hour) |
| `input_number.ev_assumed_charge_kw` | — | Assumed charge power when sensor unavailable |
| `input_number.ev_transfer_fee_eur_kwh` | 0.06387 | Transfer fee added to spot price |

### Key status sensors

| Entity | Description |
|--------|-------------|
| `sensor.ev_schedule_check` | Human-readable mode + slot status string |
| `sensor.ev_remaining_used_kwh` | kWh still needed for smart-plan target |
| `sensor.ev_min_charge_target_kwh` | kWh needed for minimum SOC |
| `sensor.ev_min_charge_deadline_ts` | Unix timestamp of next min-charge deadline |
| `sensor.ev_planned_cost_eur_v3` | Estimated total session cost (EUR) |
| `sensor.ev_planning_horizon_end_ts` | End of available price data (dynamic — tracks full dataset) |
| `binary_sensor.ev_should_charge_now_combined` | Master charge signal (all modes) |
| `binary_sensor.ev_warning_not_enough_slots` | Smart plan cannot reach target by deadline |
| `binary_sensor.ev_warning_min_charge_not_enough_slots` | Min-charge plan cannot reach target |

---

## Dashboard

The Lovelace dashboard (`lovelace/ev_charging_dashboard.json`) has three views:

- **EV Charging** — full desktop view with all controls, charts, and cost history
- **EV Charging (Mobile)** — compact view for phone use
- **debug** — raw sensor dump for troubleshooting

To install: open your EV Charging dashboard → three-dot menu → Edit dashboard → Raw configuration editor → paste the JSON.

See [dashboard.md](dashboard.md) for a card-by-card breakdown.

---

## How Pricing Works

```
Nordpool spot (15 min slots)  ──┐
                                ├─► ev_price_slots_15m_effective  ──► ev_plan_15m_rank_effective
Nordpool Predict FI             │   (merged, globally ranked)         (deadline-bounded
  └─► ev_predict_hourly_series  │                                      cheapest N slots)
  (all hours, c/kWh → EUR/kWh) │
  └─► ev_predict_15m_series ───┘
  (expanded to 4×15m per hour)
```

When `ev_allow_predicted_hours = on`, predicted slots beyond the spot horizon (typically today +
tomorrow) are appended to the merged price series. The deadline can be pushed all the way to the
**end of the Nordpool Predict FI dataset** — typically 4–8 days ahead — and the planner will pick
the cheapest slots across that entire window.

The effective planning horizon is tracked dynamically by `sensor.ev_planning_horizon_end_ts` and
the deadline guard clamps to it automatically. No slots are excluded or truncated beyond the actual
data availability.

> **Note — legacy guard:** `ev_prices_effective_and_plan.yaml` contains an older automation
> (`ev_deadline_guard_clamp_to_predict_horizon`) that still caps the deadline to a hardcoded
> `now + 12 h`. This conflicts with the newer architecture. Until it is removed, deadlines
> will be silently clamped to 12 h even when predicted hours are allowed. See [architecture.md](architecture.md)
> for details and the removal procedure.

---

## Min-charge Deadline

The minimum charge mode uses a **rolling daily deadline**: the next occurrence of
`ev_min_charge_by_hour` (default 08:00). If it is currently before 08:00, the deadline is today
at 08:00; if it has already passed, the deadline is tomorrow at 08:00. This always gives the
charger at least one overnight window to find cheap slots.

The min-charge plan searches within `[now … min(min_deadline, planning_horizon)]`, so it is
also bounded by the available price data. Predicted slots are included if
`ev_allow_predicted_hours = on`.

---

## Cost Tracking

Two parallel systems:

1. **Real-time accumulator** (`ev_session_cost_v3.yaml`): fires on every energy meter change → `delta_kwh × price_now_eur_kwh` → `ev3_session_cost_actual_eur`. Accurate for all modes.
2. **Forward estimate** (`ev3_estimated_cost_eur`): projects remaining planned slots × price. Shown as `sensor.ev_planned_cost_eur_v3`. Only covers the smart plan; min-charge slots use the real-time accumulator.

Session history: cost is saved to `input_number.ev_session_cost_last_eur` at each plug-out. Statistics sensors compute 7-day and 30-day mean/max.

---

See [architecture.md](architecture.md) for the full technical reference.
