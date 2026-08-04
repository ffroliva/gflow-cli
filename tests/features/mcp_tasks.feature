Feature: MCP Tasks Extension (SEP-2663)
  As an MCP client driving gflow generations
  I want long-running image and video generations to return a task handle immediately
  So that I can poll task status asynchronously and cancel jobs without holding connections open.

  Scenario: Non-blocking generation returns a task handle immediately
    Given a running gflow MCP server
    When an MCP client invokes "gflow_generate_image" with prompt "futuristic neon skyline"
    Then a task is enqueued in the SQLite generation queue
    And the tool call returns a task handle with status "pending".

  Scenario: Polling task status via tasks/get
    Given an enqueued generation task with ID "task-uuid-123"
    When the MCP client sends a "tasks/get" request for "task-uuid-123"
    Then the server returns the current task status and details.

  Scenario: Canceling an in-flight generation task
    Given a running generation task "task-uuid-456" holding a profile lease
    When the MCP client sends a "tasks/cancel" request for "task-uuid-456"
    Then the task status is updated to "failed"
    And the profile lease is released cleanly.

  Scenario: Requesting status for an unknown task ID
    Given no task exists with ID "non-existent-task"
    When the MCP client sends a "tasks/get" request for "non-existent-task"
    Then the server returns a TaskNotFoundError with error code -32602.

  Scenario: Legacy blocking tool execution
    Given an MCP client requesting blocking execution with "wait=true"
    When the client invokes "gflow_generate_image" with prompt "classic portrait"
    Then the tool call blocks until generation completes and returns asset paths.
