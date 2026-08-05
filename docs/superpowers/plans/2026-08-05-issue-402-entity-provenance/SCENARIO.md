# Scenario: Record character entity provenance on generation operations (#402)

## Coverage Map
- Active dimensions: **D6** (Data layer persistence & `metadata_json`), **D7** (Error propagation), **D8** (CLI UX flag parity).
- Skipped dimensions: **D1**, **D2**, **D3**, **D4**, **D5**, **D9**, **D10**, **D11**, **D12**.

## Scenario Table

| # | Dimension | Scenario | Severity | Expected behaviour | Test category |
|---|---|---|---|---|---|
| 1 | D6 Data layer | `image i2i` / `t2i` with `--reference-entity` records `entity_ids` and `entity_names` in `operations.metadata_json` | High | `metadata_json` contains `{"entity_ids": [...], "entity_names": [...]}` | Unit |
| 2 | D6 Data layer | `video r2v` / `t2v` / `i2v` with `--reference-entity` records `entity_ids` and `entity_names` in `operations.metadata_json` | High | `metadata_json` contains `{"entity_ids": [...], "entity_names": [...]}` | Unit |
| 3 | D8 CLI UX | `gflow video r2v` / `t2v` / `i2v` accepts `--reference-entity` and `--reference-entity-name` CLI flags | High | CLI parses repeatable `--reference-entity` flags without usage error | Unit / Integration |

## Must-Cover Before Merge
1. Assert `metadata_json` contents on `record_generated_images` and `record_started_video` when reference entities are present.
2. Assert `gflow video` subcommands accept `--reference-entity` and `--reference-entity-name` options.

## Suggested BDD Scenarios (`tests/features/entity_provenance.feature`)

```gherkin
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
```
