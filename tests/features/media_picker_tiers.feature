Feature: Media picker resolves UUID references without dead search tiers
  The picker's full-UUID and UUID-stem search tiers are live-proven never to
  match (#287, #393). They must not run before the scroll fallback, and the
  reference-binding contract must be unchanged.

  Scenario: A bare UUID reference off the viewport is found by scrolling
    Given an image i2i generation with one bare "--ref <uuid>"
    And the reference tile is not in the picker's initial viewport
    When the transport binds the reference
    Then no search term is typed before the grid is scrolled
    And the tile is attached from the scroll tier
    And the attach event reports resolved_by "scroll"

  Scenario: An unreachable reference still refuses to generate without it
    Given an image i2i generation with one bare "--ref <uuid>"
    And the reference tile is absent from the picker entirely
    When the transport binds the reference
    Then the demoted UUID search tiers are attempted after the scroll
    And the recorded local file is uploaded as the fallback
    And the generation never proceeds without the reference

  Scenario: A frame-slot UUID that cannot be located fails pre-generation
    Given a video i2v generation with an initial frame given as a media UUID
    And the asset is absent from the picker entirely
    When the transport binds the frame
    Then a TransportTimeoutError naming the slot and the UUID is raised
    And the process exits with code 9
    And no generation is submitted

  Scenario: A picker cohort with no search box is never blocked by search
    Given an image i2i generation with one bare "--ref <uuid>"
    And the picker variant renders no search input
    When the transport binds the reference
    Then no search input is filled at any point
    And the grid is scrolled to locate the tile
