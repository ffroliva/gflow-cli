Feature: Locale-free character entity attach (issue 170)
  The picker include selectors are locale-free cascades; the CLI surface must
  pass reference entities through, fail typed-and-locale-neutral when the
  include action never appears, and refuse to report success when the staged
  entity did not ride the wire (submit backstop).

  Scenario: t2i passes the reference entity to the generation runner
    Given the mocked t2i runner records the request and writes one image
    When I run "gflow image t2i a knight --project proj-1 --reference-entity ent-123 --reference-entity-name Lukas"
    Then the exit code is 0
    And the runner received reference entity "ent-123" named "Lukas"
    And one image file is created

  Scenario: missing include action fails typed and locale-neutral
    Given the mocked t2i runner raises the include-action timeout
    When I run "gflow image t2i a knight --project proj-1 --reference-entity ent-123 --reference-entity-name Lukas"
    Then the exit code is 9
    And the output contains "include action"
    And the output does not contain "Incluir no comando"

  Scenario: entity absent from the captured submit payload fails loudly
    Given the mocked t2i runner raises the reference-entity submit backstop
    When I run "gflow image t2i a knight --project proj-1 --reference-entity ent-123 --reference-entity-name Lukas"
    Then the exit code is 7
    And the output contains "issues/174"
