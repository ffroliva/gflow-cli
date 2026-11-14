@e2e @e2e_auth
Feature: Credits are read without a browser when the profile's cookies are accepted (#1229)
  The direct read uses the profile's saved cookies, one page GET and one batchexecute POST.
  When Flow does not accept them, the browser read answers instead.

  Scenario: A healthy profile reads its balance without launching Chrome
    Given the e2e profile is signed in to Flow
    When I run "gflow credits user --json"
    Then the balance is a non-negative integer
    And no Chrome process was started for the profile
    And at most one page GET and one batchexecute POST were sent

  Scenario: Cookies Flow does not accept fall back to the browser read
    Given the e2e profile is signed in to Flow
    And the wire read is forced to send cookies Flow does not accept
    When I run "gflow credits user --json"
    Then the balance is a non-negative integer
    And the wire fell back with reason "signed_out_shell"

  Scenario: The browser session survives a run of wire reads
    Given the e2e profile is signed in to Flow
    And I have read the balance over the wire 5 times
    When I run "gflow auth status"
    Then the profile's Flow session is reported valid
    And a browser read of the balance still succeeds
