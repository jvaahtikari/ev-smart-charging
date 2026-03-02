# EV Smart Charging — Roadmap

This document describes planned enhancements beyond the current system.
Each phase is independent and can be implemented separately.

---

## Phase 1 — ML consumption prediction → adaptive SOC target ✅ COMPLETED

**Goal:** Replace the manually set `ev_target_kwh` / `ev_max_soc` with a
learned prediction of how much energy the driver will actually consume
before the next charging session. The system charges exactly what is needed
rather than a fixed amount, avoiding both over-charging (battery degradation,
wasted cost) and under-charging (range anxiety).

**Status: Implemented** (2026-03)

The final implementation uses an **exponentially weighted average (EWA, α=0.15)**
per consumption profile rather than a gradient-boosted regression. Profiles are
keyed on `season|temp_band|drive_type|trip_type|preheating` (~48 realistic
combinations). This approach is simpler, fully explainable, requires no training
infrastructure, and works with a small number of trips (5 per profile).

**What was built (`smart-ev-learning` AppDaemon module):**
- `ev_trip_logger.py` — records each drive segment (SOC, km, temp, preheat)
- `model_updater.py` — builds the EWA consumption model from trip history
- `predictor.py` — publishes 5-day SOC forecast to `sensor.ev_consumption_forecast`

**Integration (`ev_learning_bridge.yaml`):**
- On plug-in: auto-sets `input_number.ev_target_kwh` using price-responsive logic
  - Cheapest window slot < `ev_bargain_price_threshold` (default 3 c/kWh) → fill to ceiling
  - Otherwise → minimum kWh for trips by deadline (from 5-day forecast)
- Never writes to `ev_max_soc` — user controls the ceiling permanently
- Reliability warning when deadline is beyond the 3-day weather forecast horizon

**Entities delivered:**
- `sensor.ev_consumption_forecast` — state = tomorrow SOC%; attributes: `days[]`,
  `cumulative_soc_pct[]`, `cumulative_kwh[]`, `forecast_reliable_days`
- `sensor.ev_learning_forecast_soc` — minimum SOC needed for trips by deadline
- `sensor.ev_learning_min_window_price` — cheapest slot in charging window (c/kWh)
- `input_number.ev_bargain_price_threshold` — opportunistic-fill threshold
- `binary_sensor.ev_forecast_reliability_warning` — deadline beyond reliable horizon
- Dashboard: new "Charging Strategy" card with SOC trajectory chart (2-day history +
  4-day forecast), per-day forecast tiles, mode banner, and bargain threshold slider

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

## Phase 4 — Long journey mode (full charge to 100 %)

This phase is a third independent deadline alongside the existing smart
deadline and the daily minimum-charge deadline. It is delivered in two steps.

---

### Phase 4 — Step 1: manual third deadline (no calendar)

**Goal:** The driver can manually activate a "charge to 100 %" mode with a
deadline datetime. The system plans the cheapest slots to reach 100 % by
that time, overlaid on the normal smart plan. A simple toggle enables or
disables it.

This is intentionally the simplest useful implementation. No calendar
integration, no keyword matching — just a third deadline the driver sets
when they know a long trip is coming.

**New entities:**
- `input_boolean.ev_full_charge_mode` — toggle on/off
- `input_datetime.ev_full_charge_deadline` — by when to reach 100 %
- `sensor.ev_full_charge_plan` — cheapest slots to reach 100 % by deadline
- `binary_sensor.ev_should_charge_now_full` — current slot in full-charge plan

**Operating mode priority (revised):**
```
1. DISABLED
2. CHARGE NOW override
3. FULL CHARGE mode   ← new, sits above min-charge and smart
4. MIN CHARGE
5. SMART
6. IDLE
```

**Behaviour:**
- When `ev_full_charge_mode = on`, the full-charge plan calculates slots
  needed to go from current SOC to 100 % by `ev_full_charge_deadline`
- The plan uses `ev_price_slots_15m_effective` (spot + forecast) so the
  deadline can be days ahead
- If the deadline is unreachable (not enough slots), a warning sensor fires
- When SOC reaches 100 % or the deadline passes, a notification prompts the
  driver to turn off the mode (auto-off is optional but risky — leave manual)
- Normal smart plan continues to run in parallel; the full-charge plan simply
  adds extra slots on top

**Snapshot / restore:** Not needed for Step 1 — the toggle is explicit and
the driver turns it off after the trip.

---

### Phase 4 — Step 2: calendar trigger

**Goal:** Automate Step 1 activation by watching Home Assistant calendar
entities for events matching configurable keywords (e.g. "business trip",
"long drive"). When a match is detected within the lookahead window, the
system automatically sets `ev_full_charge_deadline` to `event_start − buffer`
and turns on `ev_full_charge_mode`.

**New entities (additional to Step 1):**
- `input_text.ev_journey_keywords` — comma-separated keywords to match
- `input_number.ev_journey_departure_buffer_min` — buffer before departure (default 30)
- `input_number.ev_journey_lookahead_hours` — how far ahead to scan (default 48)
- `input_text.ev_journey_event_name` — name of the detected event (for display)

**Calendar integration:**
Uses `calendar.get_events` service (available since HA 2023.11). An
automation triggers on a 15-min time pattern and on calendar state changes,
queries upcoming events, checks for keyword matches, and activates the mode.
After the event start time passes, the mode is auto-disabled.

---

---

## Design study — rolling horizon model

*This is an open architectural question to resolve before or alongside
Phase 1. It does not need to be answered for Phases 4 or 2.*

### The current model: session-based, fixed deadline

The system today is session-oriented:

```
plug in → set target kWh + deadline → planner picks cheapest slots → unplug
```

The user explicitly sets how much to charge and by when. This is simple,
transparent, and predictable — but it places cognitive load on the driver
(they must remember to set a sensible deadline and target every session).

### The alternative: rolling / moving deadline

Once Phases 1 (ML consumption) and 4 (full-charge mode) are in place, the
system has three independent SOC targets running simultaneously:

- A **minimum floor** (daily, from min-charge mode)
- A **normal operational target** (how much energy for the coming days)
- A **ceiling** (100 % for known long trips)

The question is whether the "normal operational target" should remain
session-based or shift to a **rolling continuous model**:

```
Instead of: "charge X kWh by time T"
Become:     "keep SOC ≥ predicted_need_pct over the next N days,
             using cheapest slots from the full forecast window"
```

In this model there is no explicit deadline. The system continuously
re-evaluates: *given predicted consumption over the next rolling N days
and the full price forecast, what are the cheapest slots that keep the
car ready?* It selects the globally cheapest slots across the whole
horizon, charging less when prices are expected to fall and pre-charging
when prices are expected to spike.

**Advantages:**
- Fully automatic — driver never sets a deadline or target
- Exploits the full multi-day forecast window naturally
- Naturally integrates with ML consumption prediction (Phase 1): the rolling
  need is the ML output
- Eliminates the "I forgot to update my deadline" failure mode

**Challenges and open questions:**
- How is the rolling window length chosen? 3 days? 7 days? User-configured?
- How does the system handle uncertainty — if consumption prediction is wrong,
  does it leave the car under-charged?
- The min-charge daily deadline becomes the hard floor; the rolling model
  operates above it. Is that separation clear enough to users?
- Battery health: continuous keep-at-80% is better than repeated 20→100%
  cycles. The rolling model could enforce a soft ceiling (e.g. 85 %) except
  when full-charge mode is active.
- How does the driver understand what the system decided and why? A rolling
  model is harder to explain in a status string than "charging 3 kWh by
  Tuesday 08:00".
- The current architecture uses `input_datetime.ev_deadline` and
  `input_number.ev_target_kwh` as the planning inputs. A rolling model would
  replace these with `sensor.ev_predicted_need_kwh` (from Phase 1) and a
  computed rolling horizon timestamp. The existing planner core
  (`ev_plan_15m_rank_effective`) is largely reusable — only its inputs change.

**Suggested study approach:**
Run both models in parallel for a period — the session-based plan as
`ev_plan_15m_rank_effective` and the rolling model as a new shadow sensor
`ev_plan_rolling_effective`. Log both sets of planned slots and compare
actual cost, SOC coverage, and slot selection patterns over weeks. Let the
data drive the decision.

---

## Priority order

| Item | Effort | Value | Status |
|------|--------|-------|--------|
| 1 — ML consumption prediction | Medium | High (cost + autonomy) | ✅ **DONE** |
| 4 Step 1 — manual full-charge mode | Low | High (safety / range) | **Next** |
| 4 Step 2 — calendar trigger | Low–medium | High (convenience) | After 4.1 |
| Design study — rolling horizon | Low (analysis only) | Strategic | Alongside Phase 4 |
| 2 — Grid load awareness | Medium–high | High (infrastructure safety) | Future |
| 3 — HTML web UI | High | Medium (UX polish) | Last |

Phase 1 (ML) is complete. Phase 4 Step 1 is next: pure YAML / automation,
provides immediate practical value (safe 100% charge for long trips), and
does not depend on any other phase. Phase 2 can share the AppDaemon
infrastructure built for Phase 1. Phase 3 is an independent UI rewrite
that can happen at any time.
