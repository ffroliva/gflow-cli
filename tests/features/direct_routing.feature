Feature: Route and refusal retry for direct generation
  Direct HTTP generation uses a headless browser to mint reCAPTCHA tokens.
  A submit Flow refuses as unusual activity is retried once from a fresh
  headless browser. Shapes the direct path cannot serve route to the UI path,
  launched headed.

  Scenario: A refused direct submit is retried once from a fresh headless browser
    Given the direct path serves the request
    And Flow refuses the first submit as unusual activity
    When the generation runs
    Then exactly two submits are sent
    And the second runs on a new headless browser process
    And no credits are reported as charged

  Scenario: A refusal on both attempts stops without a third submit
    Given Flow refuses every submit as unusual activity
    When the generation runs
    Then exactly two submits are sent
    And the error is not retryable and suggests no next action

  Scenario: A timeout after send is never resent
    Given the submit times out after the body was sent
    When the generation runs
    Then exactly one submit is sent
    And the outcome is indeterminate and the project is reconciled

  Scenario: A signed-out profile reports the UI path's auth error with no retry
    Given the profile's saved session is signed out
    When the generation runs
    Then no submits are sent
    And the UI auth error is reported with no retry

  Scenario: An explicit headed setting with no display fails before launch, naming the display
    Given an explicit headed browser setting without a display
    When the client launches
    Then the launch fails before launch naming the missing display

  Scenario: A credit-capped video takes the UI path
    Given a video request at a measured key with a credit cap
    When the route is chosen
    Then the UI path is chosen and launched headed

  Scenario: An unmeasured shape takes the UI path headed
    Given an image-to-video request with an end frame
    When the route is chosen
    Then the UI path is chosen and launched headed

  Scenario: GFLOW_CLI_HEADLESS=false keeps the auto default
    Given the environment variable GFLOW_CLI_HEADLESS is set to "false"
    When the route is chosen
    Then the launch visibility is headless
