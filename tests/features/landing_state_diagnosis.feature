@e2e @e2e_auth
Feature: A known landing state is named, never reported as selector drift
  Flow can answer a navigation with a page that is not the one we asked for — its
  public /about landing, or a NextAuth sign-in error on the app's own origin. The
  readiness wait then times out on an anchor that was never going to be there, and
  blames the anchor. That sends the operator to "check for a newer release, then
  file a bug" over a session state no release changes.

  flow_host_kind() cannot see this: it classifies the ORIGIN, and /about, the
  project page and the sign-in error page all share one. Reported at three sites
  (#756, #773, the 2026-09-10 RED canary) and special-cased three times before
  (#721 credits, #749 agent mode, FlowAppError's crash page).

  Background:
    Given a real browser whose Flow requests are served locally, spending nothing

  @e2e @e2e_auth
  Scenario: the migrated editor is answered with Flow's public landing page
    Given a project URL on the migrated host
    When Flow redirects it to its public /about landing page
    Then the failure names the landing page and the project it did not open
    And the failure is not reported as selector drift
    And the failure does not assert why the redirect happened
    And the failure is not flagged retryable

  @e2e @e2e_auth
  Scenario: the labs gallery is answered with a NextAuth sign-in error
    Given the labs Flow gallery URL
    When Flow answers it with a NextAuth sign-in error page
    Then the failure says the session is signed out
    And the failure does not blame the New project anchor

  @e2e @e2e_auth
  Scenario: an ordinary missing anchor is still reported as selector drift
    Given a project URL on the migrated host
    When Flow serves the project page but the settings trigger never appears
    Then the failure is reported as selector drift

  # Measured 2026-09-15 on a new free Google account: Flow renders a dedicated
  # Angular component, <flow-pinhole-unavailable-screen>, inside its own app shell.
  # Not a redirect — flow.google.com/ answers 200 and the hop is client-side — and
  # not a stable path either: /unavailable and /u/8/unavailable were both observed.
  # The component is the anchor; the path is neither reliable nor needed.
  @e2e @e2e_auth
  Scenario: the account cannot reach Flow at all
    Given a project URL on the migrated host
    When Flow answers it with the unavailable screen
    Then the failure says this account cannot reach Flow
    And the failure is not reported as selector drift
    And the failure does not tell the user to sign in again
    And the failure is terminal, not retryable

  # The labs arm of the same defect, found by the 2026-09-15 council rather than by a
  # test. `_enter_editor` returns early when a project id is supplied -- no readiness
  # gate -- so the guard in its gallery arm never runs, and the failure surfaces from
  # `_switch_to_image_mode` instead. Offline because it cannot be run live here: every
  # profile we hold is served flow.google.com, so the labs arm is unreachable (a named
  # blocker, not an untested claim).
  @e2e @e2e_auth
  Scenario: the labs editor is answered with the unavailable screen
    Given a project URL on the labs host
    When Flow answers the labs editor with the unavailable screen
    Then the failure says this account cannot reach Flow
    And the failure is not reported as selector drift
    And the failure is terminal, not retryable
