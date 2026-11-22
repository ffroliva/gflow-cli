Feature: Direct HTTP transport for image generation on flow.google.com

  Scenario: Plain text-to-image uses direct HTTP transport
    Given a profile served flow.google.com
    When a plain text-to-image request runs on the direct_http transport
    Then one direct POST is sent and the image is returned with transport direct_http
