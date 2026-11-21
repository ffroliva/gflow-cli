Feature: Narrate text to a local audio file
  Scenario: Missing edge extra gives an install hint
    Given edge TTS is unavailable
    When I narrate "hello" to "missing.mp3" with provider "edge"
    Then audio narration fails with a configuration error
    And no narration file is created

  Scenario: Existing output is preserved
    Given narration output "existing.mp3" already contains "keep"
    When I narrate "hello" to "existing.mp3" with provider "edge"
    Then audio narration fails with a configuration error
    And narration output "existing.mp3" still contains "keep"

  Scenario: Gemini rejects unsupported controls before a request
    Given Gemini TTS has a configured key
    When I narrate "hello" to "gemini.wav" with provider "gemini" and rate "+10%"
    Then audio narration fails with a configuration error
    And no Gemini request is sent

  Scenario: Provider errors do not expose text or credentials
    Given Gemini TTS returns an error containing private diagnostics
    When I narrate "private narration" to "failed.wav" with provider "gemini"
    Then audio narration fails with a configuration error
    And the response omits the private text and key
