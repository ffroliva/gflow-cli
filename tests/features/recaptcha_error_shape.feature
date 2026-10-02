@e2e @e2e_auth
Feature: A reCAPTCHA mint failure is a typed error whose retry flag matches what a retry does
  `RecaptchaError` used to be a bare RuntimeError: exit 1 with no Problem Details, and it
  ended a batch past --continue-on-error (#915). It is now a GFlowError, retryable only
  where a live measurement showed a retry recovers. Free: tokens are minted and discarded.

  Scenario: A mint on a page with no reCAPTCHA script is typed and not retryable
    Given a live client whose pool page is parked at about:blank
    When the client mints a token on it
    Then the failure is a typed reCAPTCHA error that is not retryable

  Scenario: A mint that loses a race with a navigation is retryable, and the retry succeeds
    Given a live client on a Flow project page
    When a mint races a navigation of that page
    Then the failure is a typed reCAPTCHA error that is retryable
    And a mint on the settled page succeeds
