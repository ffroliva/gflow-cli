@e2e @e2e_auth
Feature: A promo modal over a fresh editor is cleared before the first click
  # #859. On the migrated host a `role="dialog"` promo panel over a freshly loaded
  # editor made `image t2i` fail as exit 23 — ".settings-trigger-button did not accept
  # a click within 5000 ms — it is covered by div.cdk-overlay-backdrop".
  #
  # `_dismiss_dialog` existed and its selector matched. The issue was filed saying the
  # dismissal was ordered AFTER the click; it is not — `ensure_editor` calls it. It
  # calls it one frame after `domcontentloaded`, before Angular has rendered anything,
  # so `is_visible()` answers False and the method returns without logging either of
  # its two events. That silence in the incident's event stream is the evidence: the
  # dismissal ran, saw nothing, and the dialog mounted afterwards.
  #
  # The consent bar had already been given the right shape one line over — dismissed
  # in `_open_pane`, where both the image and video paths take their FIRST click. The
  # promo dialog now shares it.
  #
  # These are e2e because the whole defect is WHEN the DOM has the dialog in it. A
  # mocked page is whatever the test built before the test ran; it has no "later".
  #
  # Cost: zero. Route interception serves every Flow origin — no Google, no profile.

  Scenario: a dialog that mounts after the editor is ready is dismissed anyway
    Given a Flow project page whose promo dialog appears only after the editor settles
    When the driver opens the settings pane
    Then the pane opens and nothing is raised
    And the dialog was dismissed by its close button

  Scenario: a dialog with no close button is dismissed with Escape
    Given a Flow project page whose promo dialog offers no close button
    When the driver opens the settings pane
    Then the pane opens and nothing is raised
    And the dialog was dismissed by Escape

  Scenario: a dialog that refuses to close is named, never swallowed
    # The dismissal stays best-effort by design: rather than a second error path, a
    # modal that will not go falls through to the click post-mortem, which names the
    # backdrop. One attributed failure beats two competing ones.
    Given a Flow project page whose promo dialog ignores its own close button
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message names the covering backdrop

  Scenario: an editor with no dialog is not touched
    # The A/B control, and it guards a real hazard: with no close button the dismissal
    # falls back to Escape, and an Escape pressed on a healthy page would close the
    # settings pane the very next line opens.
    Given a Flow project page with no promo dialog
    When the driver opens the settings pane
    Then the pane opens and nothing is raised
    And no dismissal gesture was made
