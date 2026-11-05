@e2e @e2e_video
Feature: A video whose submit reply body is lost is reported as possibly billed
  The video twin of migrated_submit_body_loss.feature. Before #1010 a lost `YhhmEf`
  reply body read as "no reply" and a retryable exit 9 while Flow rendered and billed the
  clip, so the advice to retry bought a second clip.

  # #1141: nothing was uploaded, so polls collect prompt-and-model matches and the project
  # listing, the one global view, picks the clip (2026-11-04 spike). Only real Flow can show
  # the listing naming a running t2v clip.
  Scenario Outline: A text-to-video clip whose submit reply body is lost is delivered, through <surface>
    Given a video submit whose reply body Chrome cannot return
    When <surface> generates a lite text-to-video clip
    Then exactly one video submit reply reached gflow
    And the clip is on disk

    Examples:
      | surface            |
      | the CLI            |
      | the MCP video tool |

  # #1112: a run that uploaded a file is followed to its clip. The first status poll
  # after the loss names the clip by the id this run uploaded (spike 2026-10-27).
  Scenario Outline: An r2v clip whose submit reply body is lost is still delivered, through <surface>
    Given a video submit whose reply body Chrome cannot return
    When <surface> generates an r2v clip from a local reference image
    Then exactly one video submit reply reached gflow
    And the clip is on disk

    Examples:
      | surface            |
      | the CLI            |
      | the MCP video tool |
