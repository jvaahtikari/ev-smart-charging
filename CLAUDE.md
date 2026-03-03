# EV Smart Charging — Claude Code Entry Point
## Read order for every session
1. This file (CLAUDE.md)
2. PROGRESS.md — check what was done, what is next
3. IMPLEMENTATION_PLAN.md — find current session brief
4. DESIGN_SPEC.md — read ONLY sections listed in session brief
## Critical rules
- All editing happens locally. SSH only for deployment after acceptance.
- AppDaemon deployment path: `/addon_configs/a0d7b954_appdaemon/apps/smart-ev-learning/`
- Never delete working configurations — comment out and keep .bak
- Every new AppDaemon script needs an apps.yaml entry
- Use utils.py ha_write() for all AppDaemon → HA entity writes
- Chart data goes to /config/www/ev-dashboard/chart_data.json, NOT HA attributes
- **Token budget:** Never read the full DESIGN_SPEC.md — read ONLY the sections listed in today's session brief. Never read full YAML packages unless editing that specific package. Grep before opening.
