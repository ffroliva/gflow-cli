Feature: Transcribe a video clip to SRT and burn in captions locally (#1358)
  gflow-cli exports SubRip (.srt) subtitles from a local video file or catalog video UUID
  through an explicitly configured OpenAI-compatible audio transcription endpoint, and
  burns captions into an .mp4 clip per frame using PyAV and Pillow without spending Flow
  credits or launching a browser.

  Scenario: Burning an authored SRT file into a local video clip runs offline
    Given a local video clip "sample.mp4" with an audio stream
    And a valid SubRip file "sample.srt" with two timed caption entries
    When the user runs "gflow video captions sample.mp4 --srt sample.srt --json"
    Then the command exits with code 0
    And the JSON output reports "sample-captions.mp4" and "frames_burned" greater than 0
    And the output video "sample-captions.mp4" preserves the audio stream
    And no transcription HTTP request is sent

  Scenario: Burning captions into a catalog video resolves the media UUID to its verified local file
    Given a catalog video asset with media UUID "11111111-2222-3333-4444-555555555555" and a verified local file
    And a valid SubRip file "sample.srt" with two timed caption entries
    When the user runs "gflow video captions 11111111-2222-3333-4444-555555555555 --srt sample.srt --json"
    Then the command exits with code 0
    And the JSON output reports the burned output video path

  Scenario: Transcribing a video clip sends extracted audio to a configured OpenAI-compatible endpoint
    Given a local video clip "speech.mp4" with an audio stream
    And GFLOW_CLI_LLM_BASE_URL is configured to an OpenAI-compatible transcription endpoint
    And the transcription endpoint returns two timed caption segments in English
    When the user runs "gflow video transcribe speech.mp4 --language en --json"
    Then the command exits with code 0
    And the SubRip file "speech.srt" is written with two numbered blocks
    And the JSON output reports "segments_count" 2 and "srt_file" ending with "speech.srt"

  Scenario: Omitting --srt on video captions transcribes first and writes both the .srt and the captioned .mp4
    Given a local video clip "speech.mp4" with an audio stream
    And GFLOW_CLI_LLM_BASE_URL is configured to an OpenAI-compatible transcription endpoint
    And the transcription endpoint returns two timed caption segments
    When the user runs "gflow video captions speech.mp4 --json"
    Then the command exits with code 0
    And the SubRip file "speech.srt" exists on disk
    And the output video "speech-captions.mp4" exists on disk with burned frames

  Scenario: Transcription is refused before sending audio when GFLOW_CLI_LLM_BASE_URL is left at the Google default
    Given a local video clip "speech.mp4" with an audio stream
    And GFLOW_CLI_LLM_BASE_URL is at its built-in Google Gemini default
    When the user runs "gflow video transcribe speech.mp4 --json"
    Then the command fails with exit code 11 and error type "https://gflow-cli.dev/errors/configuration"
    And the error envelope reports charged "no" and next_action "fix_input"
    And the remediation hint names "GFLOW_CLI_LLM_BASE_URL" and "--srt"
    And no transcription HTTP request is sent

  Scenario: Malformed or empty SRT files are refused with exit code 11 without echoing raw file content
    Given a local video clip "sample.mp4" with an audio stream
    And a malformed SubRip file "broken.srt" with invalid timestamp ranges
    When the user runs "gflow video captions sample.mp4 --srt broken.srt --json"
    Then the command fails with exit code 11 and error type "https://gflow-cli.dev/errors/configuration"
    And the error envelope reports charged "no" and next_action "fix_input"

  Scenario: The MCP tools transcribe and burn captions inside the configured output directory
    Given a local video clip "speech.mp4" with an audio stream
    And GFLOW_CLI_LLM_BASE_URL is configured to an OpenAI-compatible transcription endpoint
    And the transcription endpoint returns two timed caption segments
    When the MCP tool "gflow_video_captions" is called for "speech.mp4" with out_file "captioned/final.mp4"
    Then the MCP reply has status "ok"
    And both "output_file" and "srt_file" resolve inside the configured GFLOW_CLI_OUTPUT_DIR
