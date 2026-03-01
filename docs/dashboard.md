# EV Charging Dashboard

Dashboard key: `lovelace.ev_charging`
Config file: `lovelace/ev_charging_dashboard.json`
Frontend cards required: Mushroom, ApexCharts, Stack-in-Card, Card Mod

---

## Installing / Updating

1. Go to your EV Charging dashboard in HA
2. Three-dot menu → **Edit dashboard** → **Raw configuration editor**
3. Select all, paste the full contents of `lovelace/ev_charging_dashboard.json`
4. **Save**

> **Note**: HA cannot reload custom dashboard changes via API when Docker protection mode is enabled. The paste-in-UI method is the only reliable approach.

---

## Views

| View | Path | Purpose |
|------|------|---------|
| EV Charging | `ev-charging` | Full desktop layout |
| EV Charging (Mobile) | `ev-charging-mobile` | Compact phone layout |
| debug | `debug` | Raw sensor dump for troubleshooting |

---

## Main View Card Structure

`views[0].cards[]`:

| Index | Type | Content |
|-------|------|---------|
| 0 | vertical-stack | All controls (toggles, sliders, chips, Charge Now, Min Charge) |
| 1 | vertical-stack | Price + Planned Slots chart + slot markdown list |
| 2 | vertical-stack | Session cost history |
| 3 | entities | `sensor.ev_schedule_check` |
| 4 | apexcharts-card | 7-day electricity price + wind power |

---

## Card 0 — Controls vertical-stack

Inner cards (`cards[0].cards[]`):

| Index | Card | Entity |
|-------|------|--------|
| 0 | 2×2 grid | SOC gauge, Smart charge toggle, Deadline picker, Predict toggle |
| 1 | chips grid | Mode chip, should-charge, allow switch, charger state, horizon, power, warnings |
| 2 | 3×2 grid | Energy stats: to-100%, to-target, delivered, remaining, current price, est. cost |
| 3 | number slider | `number.ev_target_energy_kwh_ui` — target energy (kWh) |
| 4 | number slider | `number.ev_max_soc_ui` — target SOC (%) |
| 5 | number slider | `input_number.ev_assumed_charge_kw` — assumed charging power |
| 6 | vertical-stack | **Charge Now Override** section |
| 7 | vertical-stack | **Minimum Charge Level** section |

### Charge Now Override (index 6)
- Toggle: `input_boolean.ev_charge_now_override` (tap to toggle)
- Display: `sensor.ev_remaining_used_kwh` (kWh still to deliver)
- Status chip: red icon + "ACTIVE — charging to target, then auto-off" when active

### Minimum Charge Level (index 7)
- Toggle: `input_boolean.ev_min_charge_enabled`
- Display: `sensor.ev_min_charge_target_kwh` (kWh needed to reach min SOC)
- Slider: `input_number.ev_min_soc_pct` (minimum SOC %)
- Slider: `input_number.ev_min_charge_by_hour` (target hour, 0–23)
- Status chip: shows next deadline timestamp + warning if not enough slots

---

## Card 1 — Price + Planned Slots

### ApexCharts card

`graph_span: 96h`, `span.start: hour`, `experimental.color_threshold: true`

**Series** (in order):

| # | Name | Type | Color | Entity / Data Source |
|---|------|------|-------|---------------------|
| 1 | Price (c/kWh) | line | color_threshold | `ev_price_slots_15m_effective.data` + transfer fee 6.7 c/kWh |
| 2 | Planned slots | column | default | `ev_plan_15m_rank_effective.planned_slots` at actual price level |
| 3 | Min charge slots | column | orange 70% | `ev_min_charge_plan.planned_slots` at actual price level; hidden when `ev_min_charge_enabled = off` |
| 4 | Deadline | line | red dashed | `input_datetime.ev_deadline` — vertical line `[[ts,0],[ts,100],[ts+1,null]]` |
| 5 | Min charge deadline | line | orange dashed | `sensor.ev_min_charge_deadline_ts` — vertical line; hidden when min charge disabled |
| 6 | ` ` (range extender) | line | transparent | Returns `[[max(deadline+12h, now+36h), 0.001]]` — forces x-axis to extend dynamically |

**Price color thresholds** (c/kWh):

| Value | Color |
|-------|-------|
| < 3 | lime |
| 3–6 | limegreen |
| 6–10 | gold |
| 10–15 | darkorange |
| 15–20 | red |
| > 20 | darkred |
| > 30 | darkred |

**X-axis**: Finnish day abbreviations via `EVAL:` formatter (`su ma ti ke to pe la`); `tickAmount: 4`.

**Y-axis**: single axis, `min: 0 / max: 25 / title: c/kWh`. Deadline lines use values 0–100 which get clipped at 25 — still produces a full-height dashed line.

### Slot list markdown card

Lists planned slots from `ev_plan_15m_rank_effective.planned_slots` sorted by time with day abbreviation + price.
When `ev_min_charge_enabled = on`: appends a **"Min charge:"** section listing `ev_min_charge_plan.planned_slots` sorted by time. Shows "target met" when `ev_min_charge_target_kwh = 0`.

Price lookup: matches slot `ts` against `ev_price_slots_15m_effective.data` via DateTime string comparison.

---

## Card 2 — Session Cost History

| Card | Entity |
|------|--------|
| mushroom entity | `input_number.ev_session_cost_last_eur` — last session cost |
| mushroom entity | `input_datetime.ev_session_last_end` — last session end time |
| mushroom entity × 2 | `sensor.ev_session_cost_mean_7d`, `ev_session_cost_max_7d` |
| mushroom entity × 2 | `sensor.ev_session_cost_mean_30d`, `ev_session_cost_max_30d` |
| apexcharts-card | 30-day history of `input_number.ev_session_cost_last_eur`, grouped by day (last per day) |

---

## Card 4 — Electricity Price + Wind Power (7d)

`sensor.nordpool_predict_fi_price` — 7-day forecast chart with:
- Spot price line with colour thresholds (same scale as EV chart)
- Predicted price dashed red indicator line at forecast start
- Wind power columns (`sensor.nordpool_predict_fi_windpower`)
- Header shows: current price, next 6h average, cheapest 6h window, current wind power

---

## ApexCharts-Card Technical Notes

### EVAL: prefix rules
- ✅ Works for **formatter functions**: `xaxis.labels.formatter`, `tooltip.y.formatter`
- ❌ Does NOT work for **numeric fields**: `xaxis.max`, `xaxis.min` — passing an EVAL string to a numeric field crashes the entire chart with no error message

### Dynamic x-axis range
Use a hidden "range extender" series instead of `xaxis.max`:
```json
{
  "name": " ",
  "type": "line",
  "stroke_width": 0,
  "opacity": 0,
  "color": "transparent",
  "entity": "input_datetime.ev_deadline",
  "show": { "in_header": false, "in_legend": false },
  "data_generator": "try { const ts = hass.states['input_datetime.ev_deadline']?.attributes?.timestamp || 0; const dl12 = ts > 0 ? ts*1000+12*3600*1000 : 0; const min36 = Date.now()+36*3600*1000; return [[Math.max(dl12,min36), 0.001]]; } catch(e) { return []; }"
}
```
ApexCharts extends the x-axis to include all data points from all rendered series (even invisible ones), so this silently stretches the chart.

### color_threshold on data_generator series
Requires `experimental: { color_threshold: true }` at the card level. The threshold values compare against the **y-value** of each returned data point.

### Finnish day labels
```json
"labels": {
  "formatter": "EVAL:function(value, timestamp) { const days=['su','ma','ti','ke','to','pe','la']; return days[new Date(timestamp||value).getDay()]; }"
}
```

### Planned slot columns rendered at price level
Both "Planned slots" and "Min charge slots" return `[timestamp_ms, cents_value]` (not `[ts, 1]`). This renders each charging slot as a column reaching up to the actual price of that slot — visually making it easy to see which slots were chosen and how expensive they are relative to the price line.

### Min charge slot price lookup
`ev_min_charge_plan.planned_slots` only contains `{ts, rank}` — no price embedded. The data_generator looks up the price from `ev_price_slots_15m_effective.data` by matching `DateTime.substring(0,16)` to a locally-formatted `YYYY-MM-DDTHH:MM` string.
