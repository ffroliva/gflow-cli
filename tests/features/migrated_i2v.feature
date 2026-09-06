Feature: image-to-video on the migrated flow.google.com host
  On the new host a start frame is an in-project asset. gflow uploads the local file
  through the editor's own Upload entry, observes the app's maseQ reply for the media id,
  binds it through the Start-frame picker by file name, and asserts the eb1hJf submit
  body carries that id before treating the run as an i2v generation.

  Scenario: a moved account generates from a local start frame
    Given the editor hands the session to flow.google.com after entering the project
    And a local start frame "hero.png"
    When gflow video i2v runs with an 8 s request
    Then the composer uploads the file and the maseQ reply names a media id
    And the Start chip binds the asset listed under "hero.png"
    And the eb1hJf submit body carries that media id and an i2v model key
    And the result reports success with the workflow id

  Scenario: the frame did not bind, so nothing is submitted
    Given the picker lists no asset named "hero.png"
    When gflow video i2v runs with an 8 s request
    Then the run fails with exit 32 before any submit
    And the detail names the file and the picker

  Scenario: the app submitted a text-to-video body for an i2v request
    Given the Start chip is bound
    And the submit reply arrives on YhhmEf with a t2v model key
    When gflow video i2v runs with an 8 s request
    Then the run fails with exit 7 naming the t2v key on an i2v request

  Scenario: the upload is rejected
    Given maseQ answers 400
    When gflow video i2v runs with an 8 s request
    Then the run fails with exit 27 naming route batchexecute:maseQ
    And no submit was clicked

  Scenario: an end frame is not ported yet on a moved account
    Given the editor hands the session to flow.google.com after entering the project
    When gflow video i2v runs with a local start frame and a local end frame
    Then the run fails with exit 36 and the remediation names the end frame

  Scenario: an unmoved account with an end frame keeps the labs driver
    Given the account has not been moved and a project is given
    When gflow video i2v runs with a local start frame and a local end frame
    Then the labs driver serves the request
