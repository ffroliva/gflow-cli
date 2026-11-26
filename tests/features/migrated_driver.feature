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

  Scenario: Flow's records carry null where "CAE" used to be (#948)
    Given the editor hands the session to flow.google.com after entering the project
    And every record Flow replies with has null in its fourth slot
    When gflow video t2v runs with an 8 s request
    Then the migrated composer applies the settings and submits
    And the YhhmEf reply yields a workflow id and a media id
    And the result reports success with that workflow id

  Scenario: a status poll lists another clip's finished record first
    Given the editor hands the session to flow.google.com after entering the project
    And the status poll lists another clip's finished record before this run's
    When gflow video t2v runs with an 8 s request
    Then the migrated composer applies the settings and submits
    And the result reports success with that workflow id

  Scenario: an unmoved account is routed to flow.google.com by default for t2v
    Given the account has not been moved and a project is given
    When gflow video t2v runs with an 8 s request
    Then the migrated composer applies the settings and submits
    And the result reports success with that workflow id

  Scenario: a clip downloaded with no --out-dir is saved under tmp/, as on labs (#986)
    Given the editor hands the session to flow.google.com after entering the project
    And no output directory was given
    When gflow video t2v runs with an 8 s request and downloads the clip
    Then the clip is saved under tmp/ in the working directory

  Scenario: a client opened with --out-dir saves the clip there, as video chain does (#986)
    Given the editor hands the session to flow.google.com after entering the project
    And the client was opened with an output directory
    When gflow video t2v runs with an 8 s request and downloads the clip
    Then the clip is saved in that output directory

  Scenario: the requested axis has no control on this host
    Given the settings pane renders no duration radiogroup
    When a 10 s duration is requested
    Then the run aborts pre-submit with exit 11 and names the missing axis

  Scenario: the driver is switched off
    Given GFLOW_CLI_FLOW_HOST is labs.google
    And the editor hands the session to flow.google.com after entering the project
    When gflow video t2v runs with an 8 s request
    Then the run fails with exit 36 and the remediation names the switch

  Scenario: a terminal status poll whose body Chrome evicts is recovered from the project listing (#1484)
    Given the editor hands the session to flow.google.com after entering the project
    And the terminal status poll body is unreadable while the project listing records the finished clip
    When gflow video t2v runs with an 8 s request
    Then the migrated composer applies the settings and submits
    And the result reports success with that workflow id
