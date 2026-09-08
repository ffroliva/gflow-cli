Feature: Image generation on flow.google.com

  Scenario: Text-to-image uses the migrated Image mode
    Given an authenticated migrated profile and an existing Flow project
    When I request a supported text-to-image generation
    Then the page-owned image submission returns a generated image

  Scenario: Image-to-image binds a local reference before submit
    Given an authenticated migrated profile, an existing project and a local image
    When I request image-to-image with that local image
    Then the outgoing image request contains the uploaded reference

  Scenario: An unmeasured migrated reference form is refused before billing
    Given a migrated image request using a Flow media UUID
    When generation is requested
    Then the request is refused before the submit button is clicked

  Scenario: MCP preserves the migrated image request
    Given the direct and queued gflow_generate_image surfaces
    When each submits the same migrated-host request
    Then model, aspect, count and local references reach the shared transport unchanged
