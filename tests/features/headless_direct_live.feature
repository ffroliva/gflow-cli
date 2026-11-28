@e2e
Feature: Headless direct generation against Flow
  With direct_http as the default transport, a measured shape is sent over direct HTTP and
  the browser only mints the reCAPTCHA token. The route is decided before launch, so a run
  of measured shapes launches headless under the default visibility. Only a live account
  can prove Flow accepts the direct submit and the headless mint.

  @e2e_video
  Scenario Outline: A measured text-to-video runs direct at the planned visibility
    Given browser visibility "<visibility>"
    When I run text-to-video at a measured key
    Then the JSON result reports route "direct" and visibility "<launch>"
    And a 360p clip is written

    Examples:
      | visibility | launch   |
      | auto       | headless |
      | headed     | headed   |

  @e2e_video
  Scenario: A 1080p upscale of a measured clip runs direct
    Given browser visibility "auto"
    And a catalogued clip at the measured upscale source key
    When I upscale it to 1080p
    Then a 1080p clip is written

  @e2e_image
  Scenario: Four images from one headless run
    Given browser visibility "auto"
    When I run a four-row image batch
    Then four images are written
