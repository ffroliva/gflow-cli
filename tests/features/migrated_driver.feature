Feature: migrated-host driver
  Google moves accounts from labs.google onto flow.google.com one at a time. On
  the new host gflow drives the Angular editor for text-to-video: settings via
  the radios, prompt via the composer, then it OBSERVES the page's own
  batchexecute replies (YhhmEf submit, jwpduf poll, as29s result) instead of
  adding traffic of its own.

  Scenario: a moved account generates a video on flow.google.com
    Given the editor hands the session to flow.google.com after entering the project
    When gflow video t2v runs with an 8 s request
    Then the migrated composer applies the settings and submits
    And the YhhmEf reply yields a workflow id and a media id
    And a reply with status 3 yields a flow-content.google URL
    And the result reports success with that workflow id

  Scenario: the requested axis has no control on this host
    Given the settings pane renders no duration radiogroup
    When a 10 s duration is requested
    Then the run aborts pre-submit with exit 11 and names the missing axis

  Scenario: the driver is switched off
    Given GFLOW_CLI_FLOW_HOST is labs.google
    And the editor hands the session to flow.google.com after entering the project
    When gflow video t2v runs with an 8 s request
    Then the run fails with exit 36 and the remediation names the switch
