Feature: The credits read falls back to the browser whenever the direct read fails (#1229)
  gflow first reads the balance directly from flow.google.com with the profile's saved
  cookies. A failure of that read is classified, logged by reason, and answered by the
  browser read. Flow throttling (HTTP 429/5xx) is the exception: it is reported, never
  answered by a page load that would send the same requests.

  Scenario Outline: A direct-read failure falls back to the browser read
    Given the direct read fails with "<reason>"
    When I read the credits of the profile
    Then the balance comes from the browser read
    And the log records a wire fallback with reason "<reason>"

    Examples:
      | reason           |
      | signed_out_shell |
      | http_401         |
      | http_400         |
      | unparseable      |
      | store_unreadable |
      | network          |
      | redirect         |

  Scenario: A healthy direct read never starts the browser
    Given the direct read succeeds with 42 credits
    When I read the credits of the profile
    Then the balance is 42
    And the browser read never ran
