Feature: gflow character create
  # Mirrors tests/cli/test_cli_character_create.py + tests/services/test_character_create_saga.py,
  # but expressed as user-facing BDD scenarios over a fully MOCKED client/recorder/saga
  # (no live Playwright — see tests/features/conftest.py tripwires).
  #
  # Layer notes (see test_character_create_steps.py docstring for the full rationale):
  #   * "Happy create"  is driven at the CLI entrypoint (saga mocked) — matches the
  #     character_read.feature harness exactly.
  #   * "Foreign workflow -> error" is driven at the CLI entrypoint (saga raises
  #     WireFormatError) — proves the exit-code mapping AND that the command surfaces
  #     a non-zero exit without crashing.
  #   * "Resume - no re-spend" is driven at the SAGA layer (real saga, mocked client +
  #     recorder). The recorder's resume read (find_incomplete_character) and the
  #     gen-call-count invariant are saga-internal; driving them through the CLI would
  #     only re-mock the saga and assert nothing about resume. See the step docstring.

  Scenario: happy create prints the entity id and a workflow id
    Given a mocked saga that returns a created character
    When I run "gflow character create --project P --name X --face-prompt a face"
    Then the create exit code is 0
    And the output contains the created entity id
    And the output contains the created workflow id

  Scenario: resume does not re-spend a credit for an already-generated face
    Given an incomplete prior character op with the face workflow already recorded
    When the create saga runs again for the same project and name
    Then the face image is not generated a second time
    And no second credit is spent on the face

  Scenario: a foreign workflow aborts the command without patching the entity
    Given a mocked saga that raises a foreign-workflow wire error
    When I run "gflow character create --project P --name X --face-prompt a face"
    Then the create exit code is 7
    And the entity is not patched
