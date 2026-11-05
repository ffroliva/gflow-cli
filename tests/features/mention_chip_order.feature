@e2e @e2e_auth
Feature: Mention chips land in the order they were requested (#997)
  # Measured on flow.google.com (docs/superpowers/spikes/2026-10-19-fourth-ref-caret.md,
  # 3/3): before every `@` the driver clicked the composer's centre. With three chips on
  # the line the centre IS chip 2, the caret stayed behind it, and reference 4 was
  # inserted between chips 2 and 3 — the prompt read 1, 2, 4, 3. The guards counted
  # chips, so nothing noticed. The fix collapses the caret to the end of the composer
  # before every `@`, and refuses a mention that does not land after the chips already
  # there.
  #
  # An e2e because the defect IS the browser's hit-testing and selection model: where a
  # click on a line of chips puts the caret. A faked page puts it wherever its author
  # decided. Cost: zero — existing project images are mentioned in place, nothing is
  # uploaded, nothing is submitted, and the composer is cleared afterwards.
  # Needs GFLOW_CLI_E2E_PROJECT: a classic-composer project holding >= 4 captioned images.

  Scenario: four existing project images mentioned in sequence keep their order
    Given a flow.google.com project holding at least four captioned images
    When the driver mentions four of them in order without submitting
    Then the prompt's chips read in the order they were requested
    And nothing was submitted and the composer was cleared

  # #1143: with no picker open Enter submits, so a miss must never press it, and Escape
  # is what removes the typed query (2026-11-04 spike). Only a real picker can show that.
  Scenario: a mention that matches nothing is refused and leaves the attached chip alone
    Given a flow.google.com project holding at least four captioned images
    When the driver attaches one image and then mentions a name that matches nothing
    Then the mention is refused with the chip kept and no query text left behind
    And nothing was submitted and the composer was cleared
