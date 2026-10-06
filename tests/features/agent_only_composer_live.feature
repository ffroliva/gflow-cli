@e2e
Feature: gflow drives Flow's agent-only composer (#799)
  Some accounts on flow.google.com are served a composer whose only prompt box is a chat
  agent. gflow sets the run's aspect, count and model in Agent settings, asks the agent in
  plain language, reads completion from the page, and restores those defaults afterwards.
  Only a live account in that cohort can prove it: the settings pane, the agent's reply and
  the grid tiles are Flow's, and the fixtures in tests/api/transports only mirror them.

  Background:
    Given a profile whose Flow project serves the agent-only composer

  @e2e @e2e_image
  Scenario: text-to-image through the CLI returns the requested images
    When `gflow image t2i` runs with aspect 3:4 and count 2
    Then it exits 0 with 2 images in a 3:4 shape
    And the run restored the Agent-settings defaults

  @e2e @e2e_image
  Scenario: text-to-image through the MCP tool drives the same composer
    When the MCP tool gflow_generate_image runs with aspect 1:1 and count 1
    Then the tool reports completed with 1 image file

  @e2e @e2e_video
  Scenario: text-to-video through the CLI returns one MP4
    When `gflow video t2v` runs for 4 seconds at 9:16
    Then it exits 0 with one portrait MP4
    And the run restored the Agent-settings defaults
