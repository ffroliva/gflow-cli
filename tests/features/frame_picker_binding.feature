@e2e @e2e_auth
Feature: The Frames picker binds the asset it was searched for
  # #860. `_pick_frame_by_name` matched a picker option with an ANCHORED regex
  # (`_exact` -> `^\s*<name>\s*$`). A picker tile is a `mat-icon` ligature followed by
  # the file name, and a locator's text is the concatenation of BOTH nodes — the
  # picker offers `imageshero-ab12cd34.png` for an asset actually named
  # `hero-ab12cd34.png`. The anchors can never hold, so every i2v run on this cohort
  # died with exit 32, "the frame picker lists no asset named ...", while the picker
  # had been listing it the whole time. The reported listing is what pinned it: the
  # `image` prefix is the icon, not a rename.
  #
  # These are e2e because the defect IS the browser's text model. A faked locator
  # returns whatever list the test author put in it, so a mocked picker passes
  # against the broken matcher — the most comfortable wrong answer in this repo
  # (Bug Lane, step 5). Only a real DOM concatenates the icon into the name.
  #
  # Cost: zero. Every Flow origin is served by Playwright route interception, so
  # nothing reaches Google, no profile is needed and no credit is spent.

  Scenario: an option whose tile renders an icon before the name still binds
    Given a Frames picker listing the uploaded frame
    When the driver picks the uploaded frame by name
    Then the picker's own text content concatenates the icon into the name
    And the Start chip binds the uploaded frame and nothing is raised

  Scenario: an older copy of the same file is never bound
    # #792's guarantee, which `_exact` was the enforcement of and containment must
    # not give away: the uploaded name carries a run-unique tag, so a library still
    # holding yesterday's copy of the same stem cannot win the match. The picker's
    # search box does no filtering here on purpose — the locator is what must
    # discriminate, and a stub that pre-filters would prove nothing.
    Given a Frames picker listing an older copy of the same file
    When the driver picks the uploaded frame by name
    Then the Start chip binds the uploaded frame and nothing is raised

  Scenario: an asset the picker does not list is still refused
    # The A/B control. Without it the two scenarios above would also pass against a
    # matcher that binds the first option it sees, which would silently submit some
    # other asset as the start frame.
    Given a Frames picker that never lists the uploaded frame
    When the driver picks the uploaded frame by name
    Then it fails with exit 32 naming the file and what the picker did list
    And no chip was bound
