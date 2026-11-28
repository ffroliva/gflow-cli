@e2e @e2e_auth
Feature: Migrated video i2v binds in-project image UUID frames without uploading
  On flow.google.com, an image already recorded in the target --project can be bound
  directly onto the Start and/or End frame chips via its project-grid /asb/<token>,
  with zero maseQ upload (#1542). Both scenarios intercept and abort the outgoing
  batchexecute submit before it leaves Chromium, so the regression spends zero credits.

  Scenario: Start-only in-project UUID binds without uploading and sends eb1hJf carrying its media id
    Given an existing migrated Flow project with two catalogued images
    When the migrated composer binds the Start UUID frame and intercepts the submit
    Then zero maseQ uploads were sent
    And the intercepted eb1hJf submit body carries the Start media id

  Scenario: Start and End in-project UUIDs bind without uploading and send nprQif carrying both media ids
    Given an existing migrated Flow project with two catalogued images
    When the migrated composer binds the Start and End UUID frames and intercepts the submit
    Then zero maseQ uploads were sent
    And the intercepted nprQif submit body carries both Start and End media ids
