@e2e @e2e_image
Feature: A run config row generates from an earlier row's image, in place
  `"ref": "batch:N"` makes row N's generated image a reference for another row. The image
  is already in the run's project, so it is referenced where it is (by the handle Flow's
  reply returned), never downloaded and uploaded again, and the catalog records which
  image each generation was made from (#913).

  Scenario: Rows reference earlier rows without any upload, and the lineage is recorded
    Given a run config whose referencing rows point at earlier rows, one sharing its caption
    When gflow run executes it on the live profile
    Then every row succeeds and saves a real image
    And each referencing row attached its parent in place, with no upload
    And the catalog records each referencing row as image-to-image with its parent as input

  Scenario: Two rows naming the same local file upload it once and reference it in place
    Given a run config whose two rows name the same local image file
    When gflow run executes it on the live profile
    Then both rows succeed and save a real image
    And the file was uploaded once and attached in place by both rows
