# EV Smart Charging for Home Assistant

A self-contained Home Assistant package for **price-aware EV smart charging**
using Nordpool spot prices. Supports multiple charging modes, Zaptec charger
control, and a full Lovelace dashboard.

## What it does

- **Smart charging**: picks the cheapest 15-minute slots within your deadline
- **Multi-day forecast**: extends planning using Nordpool Predict FI hourly prices (typically 4–8 days); deadline can be pushed to the end of the entire predicted dataset
- **Minimum charge level**: guarantees a minimum SOC by a configurable daily hour (default 08:00)
- **Charge Now override**: temporarily bypasses slot selection to charge immediately; auto-disables when target is reached
- **Plug-in takeover gate**: stops the charger on plug-in unless the current slot is active
- **Session cost tracking**: accumulates real kWh cost for each charging session

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
```

## Requirements

- Home Assistant 2024.x+
- [Zaptec integration](https://github.com/custom-components/zaptec) — charger control
- [Nordpool integration](https://github.com/custom-components/nordpool) — spot prices
- [nordpool_predict_fi](https://github.com/...) — optional, for multi-day hourly price forecast
- HACS frontend cards: Mushroom, ApexCharts-Card, Stack-in-Card, Card Mod

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

## Key entities

| Entity | Purpose |
|--------|---------|
| `input_boolean.ev_smart_charge_enabled` | Master on/off switch |
| `input_number.ev_target_kwh` | Energy to add this session (kWh) |
| `input_number.ev_max_soc` | Target SOC ceiling (%) |
| `input_datetime.ev_deadline` | Charge-by deadline |
| `input_boolean.ev_charge_now_override` | Force charge immediately |
| `input_boolean.ev_min_charge_enabled` | Enable minimum SOC guarantee |
| `input_number.ev_min_soc_pct` | Minimum SOC target (%) |
| `sensor.ev_schedule_check` | Human-readable schedule status |
| `sensor.ev_plan_15m_rank` | Planned charging slots (attributes) |
| `binary_sensor.ev_should_charge_now_15m` | Current slot: charge? |

## Docs

See [`docs/`](docs/) for architecture details and dashboard guide.
