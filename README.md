# EV Smart Charging for Home Assistant

A self-contained Home Assistant package for **price-aware EV smart charging**
using Nordpool spot prices. Supports multiple charging modes, Zaptec charger
control, and a full Lovelace dashboard.

## Key innovations

Two design choices distinguish this system from commercial and open-source
alternatives:

**1. Forecast-extended price horizon**
Most EV schedulers are constrained to Nordpool's official day-ahead spot
prices (published ~13:00 for the following day, giving ~35 h of confirmed
data). This system additionally ingests the **Nordpool Predict FI** hourly
price forecast, which covers 4–8 days ahead. Each forecast hour is expanded
to four 15-minute slots and appended beyond the spot horizon, giving the
planner a window of several days to rank and select cheap slots. The user can
set a deadline days in advance and the system picks the globally cheapest
slots across the entire available dataset.

**2. Dual-deadline architecture**
A single deadline is not enough for real-world use. The system implements two
independent deadlines running simultaneously:

- **Smart deadline** (`ev_deadline`) — user-set, can be days ahead. The
  planner finds the globally cheapest slots within this window and charges
  gradually over the whole period. Ideal for a car that sits plugged in
  overnight or over a weekend.
- **Minimum charge deadline** (`ev_min_charge_by_hour`, default 08:00 daily)
  — guarantees that a configurable minimum SOC is always reached by the
  configured time each day, regardless of what the smart plan is doing. This
  safeguards daily usability while still letting the long-term plan optimise
  cost.

This dual-deadline approach is novel: it enables multi-day SOC top-up at
minimum cost while providing a hard safety net for daily driving needs. No
commercial charger or open-source scheduler implements both simultaneously.

---

## What it does

- **Smart scheduling**: picks the cheapest 15-minute slots within your deadline
- **Multi-day forecast**: extends planning using Nordpool Predict FI hourly prices (typically 4–8 days); deadline can be pushed to the end of the entire predicted dataset
- **Minimum charge level**: guarantees a minimum SOC by a configurable daily hour (default 08:00); runs independently of the smart plan
- **Charge Now override**: temporarily bypasses slot selection to charge immediately; auto-disables when target is reached
- **Plug-in takeover gate**: stops the charger on plug-in unless the current slot is active
- **Session cost tracking**: accumulates real kWh × real price for every session

---

## File structure

```
packages/
  ev_prices_effective_and_plan.yaml       # Spot price ingestion, 15m slot ranking, main plan
  ev_effective_predict_and_control.yaml   # Effective plan (spot+forecast merge), control automation
  ev_ui_sensors.yaml                      # UI/diagnostic template sensors & helper numbers
  ev_automations.yaml                     # All EV automations (guards, takeover, session tracking)
  ev_session_cost_v3.yaml                 # Session cost accumulator (real kWh × real price)

lovelace/
  ev_charging_dashboard.json              # Full Lovelace dashboard (raw config for UI paste)

docs/
  README.md                               # System overview
  architecture.md                         # Sensor/automation architecture reference
  dashboard.md                            # Dashboard card-by-card guide

ROADMAP.md                                # Planned enhancements
```

---

## Requirements

- Home Assistant 2024.x+
- [Zaptec integration](https://github.com/custom-components/zaptec) — charger control
- [Nordpool integration](https://github.com/custom-components/nordpool) — spot prices
- [nordpool_predict_fi](https://github.com/...) — optional, for multi-day hourly price forecast
- HACS frontend cards: Mushroom, ApexCharts-Card, Stack-in-Card, Card Mod

---

## Installation

1. Copy all files from `packages/` into your HA `config/packages/` folder
2. Ensure `configuration.yaml` has:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```
3. Adjust entity IDs for your charger (search `zag063912` → replace with your device ID)
4. Reload HA configuration
5. Import `lovelace/ev_charging_dashboard.json` via the dashboard Raw Configuration Editor

---

## Key entities

| Entity | Purpose |
|--------|---------|
| `input_boolean.ev_smart_charge_enabled` | Master on/off switch |
| `input_number.ev_target_kwh` | Energy to add this session (kWh) |
| `input_number.ev_max_soc` | Target SOC ceiling (%) |
| `input_datetime.ev_deadline` | Charge-by deadline (can be days ahead) |
| `input_boolean.ev_charge_now_override` | Force charge immediately |
| `input_boolean.ev_min_charge_enabled` | Enable minimum SOC guarantee |
| `input_number.ev_min_soc_pct` | Minimum SOC target (%) |
| `input_number.ev_min_charge_by_hour` | Daily hour for minimum SOC deadline (0–23) |
| `sensor.ev_schedule_check` | Human-readable schedule status |
| `sensor.ev_plan_15m_rank_effective` | Planned charging slots (attributes) |
| `binary_sensor.ev_should_charge_now_combined` | Master charge signal (all modes) |

---

## Docs

See [`docs/`](docs/) for architecture details and dashboard guide.
See [`ROADMAP.md`](ROADMAP.md) for planned enhancements.

---

## License

This project is released under a **custom non-commercial license** — free for personal, educational, and non-commercial use.

For **commercial use** (integration into a paid product, service, or solution delivered to third parties), a separate written license agreement is required. See [`LICENSE`](LICENSE) for full terms or contact the author to discuss commercial licensing.

© 2026 Jussi Vaahtikari
