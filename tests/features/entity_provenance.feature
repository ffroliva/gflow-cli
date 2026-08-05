Feature: Character Entity Provenance Recording
  As a user generating images or videos with character references
  I want the entity IDs and names recorded in the operation metadata
  So that I can audit character provenance in the local database

  Scenario: Record character entity metadata on image generation
    Given an image generation request with reference entity "char_123" named "Hero"
    When the image generation is recorded in the data store
    Then the operation metadata JSON contains entity_ids ["char_123"] and entity_names ["Hero"]

  Scenario: Record character entity metadata on video generation
    Given a video generation request with reference entity "char_456" named "Villain"
    When the video generation is recorded in the data store
    Then the operation metadata JSON contains entity_ids ["char_456"] and entity_names ["Villain"]
