Feature: Agentic UI Detection and Exit

  Scenario: Forced Agentic UI raises FlowAgentUiError
    Given the page DOM shows forced Agentic UI
    When I run "gflow video t2v a futuristic city"
    Then the exit code is 25
    And the output contains "Google Flow Agentic UI detected"

  Scenario: Agentic driver deduplicates 9 img nodes to 3 images
    Given an agentic page with 9 img srcs for 3 distinct UUIDs and no prior images
    When I call await_images with expected_count 3
    Then 3 GeneratedImage objects are returned
    And each image URL contains the media UUID and no THUMBNAIL param

  Scenario: Agentic settings encoded in the prompt directive
    Given an agentic driver configured with count 4 and aspect 16:9
    When I call send_prompt with text "a red apple"
    Then keyboard.insert_text was called with "4 pictures" and "16:9" in the directive

  Scenario: Agentic content-policy block raises ContentPolicyError
    Given an agentic page whose body text signals a content-policy block
    When I call await_images expecting 1 image
    Then ContentPolicyError is raised

  Scenario: Flag-only page is not a content-policy block
    Given an agentic page whose body text contains only flag affordances
    When I call await_images expecting 1 image and 1 UUID is present
    Then 1 GeneratedImage is returned without error

  Scenario: Count mismatch raises TransportTimeoutError with detail
    Given an agentic page that only ever yields 1 distinct UUID
    And the await timeout is patched to a tiny value
    When I call await_images expecting 4 images
    Then TransportTimeoutError is raised with produced and requested counts in the detail

  Scenario: Agentic driver reconciles custom instructions via REST
    Given an agentic page with a mock project ID "proj-custom-abc123de-0000-0000-0000-000000000001"
    And mock REST API response for instructions patch
    When I call configure_image_settings with custom instructions
    Then the REST PATCH request is dispatched with projectBrief cards

  Scenario: T2I CLI rejects instructions with multiple prompts
    When I run "gflow image t2i first second -i 'do X'"
    Then the exit code is 2
    And the output contains "--instruction is single-prompt only"

  Scenario: Syncing instructions containing reference image IDs
    Given an agentic page with a mock project ID "proj-relational-123"
    And mock REST API response for instructions patch
    When I call configure_image_settings with relational instructions
    Then the REST PATCH request is dispatched with imageReferenceMediaIds

  Scenario: Toggling active/inactive states of relational cards
    Given an agentic page with a mock project ID "proj-relational-456"
    And mock REST API response for instructions patch
    When I call configure_image_settings with toggled instructions
    Then the REST PATCH request is dispatched with projectBrief enabled False
