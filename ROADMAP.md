# EV Smart Charging — Roadmap

This document describes planned enhancements beyond the current system.
Each phase is independent and can be implemented separately.

---

## Phase 1 — ML consumption prediction → adaptive SOC target

**Goal:** Replace the manually set `ev_target_kwh` / `ev_max_soc` with a
learned prediction of how much energy the driver will actually consume
before the next charging session. The system charges exactly what is needed
rather than a fixed amount, avoiding both over-charging (battery degradation,
wasted cost) and under-charging (range anxiety).

**Inputs to the model:**
- Historical per-session kWh consumed (from `ev_session_cost_v3.yaml`)
- Day of week (Monday tends to need more range than Sunday)
- Calendar events for the coming days (meetings, trips, etc.)
- Season / daylight hours (winter increases consumption via heating)
- Weather forecast (temperature, wind — affects range)

**How it works:**
1. An AppDaemon or Python script trains a lightweight regression model
   (e.g. gradient-boosted tree) on historical session data
2. At each plug-in, the model predicts kWh needed before the next session
3. The prediction is written to `input_number.ev_target_kwh` automatically
4. A confidence band drives the SOC ceiling: if uncertainty is high, charge
   to a slightly higher SOC; if the pattern is very predictable, charge to
   exactly the predicted need
5. The user can always override manually; the model re-learns from the
   correction

**New entities needed:**
- `input_number.ev_predicted_consumption_kwh` — model output
- `input_number.ev_ml_confidence` — 0–1 confidence score
- `sensor.ev_ml_last_trained` — timestamp of last training run
- `input_boolean.ev_ml_enabled` — on/off toggle for adaptive mode

**Data store:** InfluxDB or a simple JSON file in `/config/` updated by AppDaemon

---

## Phase 2 — Grid load awareness → slot suppression during peak home load

**Goal:** The EV charger competes with other high-power appliances (sauna,
underfloor heating, oven, heat pump) on the home main fuse. When those
appliances run, adding 7.4 kW of EV charging can trip the breaker or cause
expensive grid demand peaks. The system should learn predictable load
patterns and either avoid those slots or reduce charge power.

**Inputs:**
- `sensor.home_grid_power_w` (or equivalent main meter entity)
- Time of day, day of week
- Season (sauna usage patterns vary)
- Thermostat schedules (heat pump predictable patterns)
- Optionally: direct state of known high-load devices (sauna switch, oven)

**How it works:**
1. A background learner records home grid draw in 15-min buckets over weeks
2. For each future 15-min slot in the price plan, it estimates the expected
   grid load at that time based on historical patterns
3. Slots where `expected_home_load + ev_charge_kw > fuse_limit_kw` are
   suppressed from the plan (treated as unavailable regardless of price)
4. When a known high-load device turns on during a charging slot, the charger
   pauses or reduces power dynamically via the Zaptec current-limit API

**New entities needed:**
- `sensor.ev_slot_home_load_forecast_kw` — predicted home load per upcoming slot
- `input_number.ev_fuse_limit_kw` — main fuse capacity (e.g. 25 kW)
- `input_number.ev_grid_headroom_kw` — reserved headroom (e.g. 3 kW buffer)
- `binary_sensor.ev_slot_suppressed_by_grid_load` — current slot suppressed?
- `input_boolean.ev_grid_load_awareness_enabled` — on/off toggle

**Integration point:** Slot suppression feeds into `ev_plan_15m_rank_effective`
as an additional filter before cheapest-slot selection. The planner already
accepts the `data[]` list; suppressed slots would be removed or given
`Rank = 9999` before the planner reads them.

---

## Phase 3 — HTML web UI

**Goal:** Replace the Lovelace dashboard with a standalone HTML/JavaScript
web page that is more flexible, more interactive, and not constrained by
Lovelace card APIs or HACS frontend dependencies.

**Advantages over Lovelace:**
- Full control of layout, typography, and interaction without card limitations
- Native SVG/Canvas price charts with custom interaction (hover, zoom, slot
  highlight, drag deadline)
- Real-time WebSocket updates from HA without polling
- Can be embedded as a custom panel in HA or served independently
- Mobile-first responsive layout without Stack-in-Card workarounds
- No HACS dependencies (Mushroom, ApexCharts-Card, Card Mod all gone)

**Architecture:**
```
Browser  ←──WebSocket──►  Home Assistant WebSocket API
                           (auth, state subscriptions, service calls)
```

The page authenticates via a long-lived token stored in `localStorage` or via
the HA OAuth flow. All sensor reads and service calls go through the standard
HA WebSocket API — no custom backend needed.

**Key UI panels:**
1. **Price chart** — SVG timeline of today + forecast prices, coloured by rank,
   with planned slots highlighted and the deadline marker draggable
2. **Mode panel** — single-tap mode switching (Smart / Min Charge / Charge Now
   / Disabled) with live status text
3. **Session panel** — live cost accumulator, SOC gauge, kWh delivered, rate
4. **Plan panel** — table of upcoming charged slots with timestamps, prices,
   and estimated cost
5. **Settings drawer** — deadline picker, target kWh, min SOC, min-charge
   hour, allow-predicted toggle

**Technology choices:**
- Vanilla JS + Web Components (no framework dependency)
- CSS Grid layout for desktop/mobile responsiveness
- HA WebSocket API (`ws://homeassistant.local:8123/api/websocket`)
- Served as a static file via HA's `www/` directory or as a custom panel

---

## Phase 4 — Long journey mode (calendar-driven full charge)

**Goal:** When the driver has a long trip scheduled (detectable from a
calendar event), the system automatically switches to a "full charge" plan
that charges the battery to 100 % by the departure time — overriding the
normal cost-optimised target. After the event passes, the system reverts to
normal mode.

**Trigger detection:**
The system monitors one or more Home Assistant `calendar.*` entities for
events that match configurable keywords (e.g. "business trip", "long drive",
"road trip", or a dedicated EV travel calendar). Keywords are configurable
via `input_text.ev_journey_keywords`.

**Behaviour:**
1. When a matching event is detected within the lookahead window (e.g. next
   48 h), the system:
   - Sets `ev_max_soc` to 100 %
   - Sets `ev_deadline` to the event start time minus a configurable buffer
     (default 30 min)
   - Sets `ev_target_kwh` to the calculated headroom to 100 %
   - Optionally enables `ev_charge_now_override` if departure is imminent
     and there is not enough time for smart scheduling
2. A persistent notification is created: *"Long journey mode active for
   [event name]. Charging to 100 % by [departure − 30 min]."*
3. After the event starts (or the battery reaches 100 %), the system
   restores the previous settings from a snapshot

**New entities needed:**
- `input_text.ev_journey_keywords` — comma-separated keywords to match
- `input_number.ev_journey_departure_buffer_min` — buffer before departure (default 30)
- `input_number.ev_journey_lookahead_hours` — how far ahead to scan (default 48)
- `input_boolean.ev_journey_mode_active` — read-only indicator, set by automation
- `input_text.ev_journey_event_name` — name of the detected event (for display)
- `sensor.ev_journey_departure_ts` — detected departure timestamp

**Snapshot / restore:**
Before overriding settings, the automation snapshots the current values of
`ev_deadline`, `ev_max_soc`, and `ev_target_kwh` to `input_text` helpers.
On restore, those values are written back.

**Calendar integration:**
Uses the standard HA `calendar.get_events` service (available since HA
2023.11) to query upcoming events without polling. An automation triggers on
time pattern (`/15 minutes`) and on calendar entity state changes.

---

## Priority order suggestion

| Phase | Effort | Value | Suggested order |
|-------|--------|-------|----------------|
| 4 — Long journey mode | Low–medium | High (safety / range) | **First** |
| 1 — ML consumption prediction | Medium | High (cost + convenience) | **Second** |
| 2 — Grid load awareness | Medium–high | High (infrastructure safety) | **Third** |
| 3 — HTML web UI | High | Medium (UX polish) | **Last** |

Phase 4 is low-risk to implement (pure HA YAML / automation), provides
immediate practical value, and does not depend on any of the others.
Phases 1 and 2 can share the AppDaemon/Python infrastructure. Phase 3 is
an independent UI rewrite that can happen at any time.
