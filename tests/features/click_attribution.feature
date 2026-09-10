@e2e @e2e_auth
Feature: A click that never lands says why
  # #776. On flow.google.com the settings trigger reads visible, the click expires after
  # 5000ms, and the run dies as a bare Playwright TimeoutError — exit 1, no locator, no
  # cause. migrated_composer.py:866-869 describes that exact failure in a comment, and
  # :884 is the line below the comment, still unguarded.
  #
  # These are e2e because Playwright's actionability gate is what fails. A mocked Page
  # whose .click() is a stub cannot express "visible, enabled, and still not clickable" —
  # it would pass against a fix that reads the DOM at the wrong moment, which is the #639
  # trap one surface over. Only a real browser can falsify this.
  #
  # Cost: zero. Every origin is served by Playwright route interception; nothing reaches
  # Google and no profile is needed.
  #
  # The driver must never name a cause it did not observe (#770 is the live precedent for
  # a confident wrong message), so "unexplained" is a first-class outcome here, not a gap.

  Scenario: A pressed agent-mode chip is named as the cause
    # #752 finding #7 predicted this before #776 was filed: agent mode hides the trigger
    # with a bare `hidden`, never with body{pointer-events:none}, so an overlay probe
    # would read "not blocked" and report the wrong thing.
    Given a Flow project page whose settings trigger is covered
    And the agent-mode chip is pressed
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message names Flow's agent mode
    And the message does not blame an overlay

  Scenario: A covering element is named by its structure
    Given a Flow project page whose settings trigger is covered
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message names the covering element by tag and structural class

  Scenario: A healthy-looking failure is reported as unexplained
    # Rules out three of Playwright's four conditions and points at the fourth (stable),
    # which is the honest answer when nothing readable is wrong.
    Given a Flow project page whose settings trigger accepts no click
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message reports the control as visible, enabled and hit-testable
    And the message does not name a cause it did not observe

  Scenario: An unobstructed trigger still opens the pane
    # The A/B control. Without it every scenario above would also pass against a helper
    # that raises unconditionally.
    Given a Flow project page whose settings trigger is clickable
    When the driver opens the settings pane
    Then the pane opens and nothing is raised

  Scenario: An account identifier on the covering element never reaches the message
    # PR #777 shipped two hours before this feature for the same bug class one surface
    # over. A typed error prints `detail` raw to the console, to structlog and to --json,
    # while the bare TimeoutError it replaces was SHA-256 hashed — so this fix REMOVES a
    # privacy net and has to put back a deliberate one.
    Given a Flow project page whose settings trigger is covered
    And the covering element carries an account email and a signed media URL
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message contains neither the account email nor the signed URL

  Scenario: A page that cannot be read still reports the failed locator
    # #722's shape: by the time the failure is diagnosed, the document the click was made
    # against is gone and the post-mortem read has nothing to answer with. A diagnostic
    # must never replace the failure it is describing.
    Given a Flow project page whose settings trigger is covered
    And the page navigates away while the click is pending
    When the driver opens the settings pane
    Then it fails with exit 23
    And the message names the settings trigger
