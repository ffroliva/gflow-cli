Feature: A character bundle is safe to share and safe to import (#639)
  A bundle is the plain folder `gflow character export` writes and `gflow character
  import` reads. It may come from anyone, so it carries nothing that ties it to an
  account, and every doubtful file is refused before anything is sent to Flow.

  Scenario: An exported manifest carries no account identifiers
    Given a character named "Nora" with voice "Aoede" and a portrait
    When its bundle is written
    Then the manifest holds only name, voice, personality and images
    And the manifest contains no URL, address, project id or entity id

  Scenario: An image path that leaves the bundle is refused
    Given a written bundle whose manifest names the image "../secret.jpg"
    When the bundle is read
    Then it is refused with exit code 11, naming the file

  Scenario: An image swapped after export is refused
    Given a written bundle whose portrait was replaced after export
    When the bundle is read
    Then it is refused with exit code 11, naming the file
