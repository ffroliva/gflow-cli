@e2e @e2e_tts
Feature: Narration can be assembled into a local video
  Scenario: Installed CLI narration produces an audible voiceover
    Given a synthetic local silent clip
    When the installed CLI creates narration audio
    And the installed CLI assembles the clip with the narration voiceover
    Then the assembled output contains non-silent voice audio

  Scenario: MCP narration remains available in no-spend mode and produces audible voiceover
    Given a synthetic local silent clip
    When the installed MCP tool creates narration audio in no-spend mode
    And the installed CLI assembles the clip with the MCP narration voiceover
    Then the assembled output contains non-silent voice audio

  Scenario: Gemini e2e skips when its key is unset
    Given the Gemini TTS key is available or missing
    When the installed CLI creates Gemini narration audio
    Then the Gemini narration audio is non-silent
