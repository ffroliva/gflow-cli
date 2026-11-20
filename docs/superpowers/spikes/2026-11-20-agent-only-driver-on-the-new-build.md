# Spike: the agent-only driver cannot reach the agent chat on Flow's 2026-11-20 build

**Date:** 2026-11-20 · **Host served:** flow.google.com · **Locale:** en-GB · **Cost:** $0
(layout clicks; the driver's pane step only; nothing typed or submitted)

Probe: [`scripts/dev/spike_agent_only_driver_new_build.py`](../../../scripts/dev/spike_agent_only_driver_new_build.py).
One profile, one project. Issues: #1369, #1387.

## Question

#1369 reports that the agent-only composer's chat answers the previous request. That can
happen only after the driver (`agent_only_composer.py`) has sent a request. The driver's
first step, `_open_pane`, clicks `SETTINGS_BUTTON` to set the run's defaults. That button is
`AGENT_ONLY_ANCHOR`, the `tune` button inside `flow-creative-agent-prompt-box`. Can the
driver get past that step on the build Flow serves today?

## Observation

A classic account was put into agent mode with the Agent switch, then the driver's own
`_open_pane` was called in two states:

| State | `SETTINGS_BUTTON` matches | `_open_pane` |
|---|---|---|
| agent mode on, panel closed | 0 | raised `UiSelectorDriftError: agent-only composer: Agent settings button (...'tune'...) missing` |
| agent mode on, panel open | 0 | raised the same |

No `tune` ligature appears on the page in either state. The agent settings gear carries
`settings_2`. The account was restored to the classic composer afterwards.

## Reading

- Matches the first pre-registered row: the driver cannot run on the 2026-11-20 build. It stops
  before typing anything, so the carry-over in #1369 cannot occur on this build.
- Both profiles probed on 2026-11-20 are served this build, and both recover to the classic
  composer (#1384). No account available here reaches the agent-only driver at all.
- The carry-over becomes reachable again only once #1387 ports the driver's settings
  anchor. The back-to-back check from #1369 belongs to that work.

## Not measured

- The 2026-11-19 build, where #1369 was observed: no profile is served it any more.
- Whether the carry-over still happens once the driver is ported.
