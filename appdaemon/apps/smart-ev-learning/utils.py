"""
utils.py — Shared utility module for EV smart charging AppDaemon scripts.
Provides ha_write() with retry logic, failure logging, and system health flag.
All EV AppDaemon scripts import and use ha_write() for HA entity writes.
"""

import time

ENTITY_SYSTEM_HEALTH = "sensor.ev_system_health"

# Write retry parameters
_MAX_RETRIES = 3
_RETRY_DELAY = 2  # seconds between retries


def ha_write(hass, entity_id, state, attributes=None):
    """
    Write state and optional attributes to a HA entity via AppDaemon.
    Retries up to _MAX_RETRIES times with _RETRY_DELAY seconds between attempts.
    On persistent failure: logs an error and sets sensor.ev_system_health to
    'write_failure' with details.

    Args:
        hass:       AppDaemon Hass instance (self in the calling script)
        entity_id:  Full HA entity_id string (e.g. 'input_text.ev_active_mode')
        state:      State value to write (will be coerced to string by HA)
        attributes: Optional dict of attributes to write alongside the state
    """
    if attributes is None:
        attributes = {}

    last_exc = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            hass.set_state(entity_id, state=state, attributes=attributes)
            return  # success
        except Exception as exc:
            last_exc = exc
            hass.log(
                f"ha_write: attempt {attempt}/{_MAX_RETRIES} failed for {entity_id}: {exc}",
                level="WARNING",
            )
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_DELAY)

    # All retries exhausted — log error and set health flag
    hass.log(
        f"ha_write: all {_MAX_RETRIES} attempts failed for {entity_id}: {last_exc}",
        level="ERROR",
    )
    try:
        hass.set_state(
            ENTITY_SYSTEM_HEALTH,
            state="write_failure",
            attributes={
                "friendly_name": "EV System Health",
                "failed_entity": entity_id,
                "error": str(last_exc),
            },
        )
    except Exception:
        # If we can't even write the health flag, there's nothing more we can do
        pass
