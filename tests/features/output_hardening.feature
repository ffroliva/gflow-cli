Feature: Custom Output Path (-o/--output) Hardening
  As a gflow-cli user or script author
  I want custom output paths to work uniformly across image and video subcommands, cloud storage, and multi-count runs
  So that generated assets land exactly at requested paths without manual file management toil.

  Scenario: Single image generation with custom nested local output path
    Given an authenticated gflow profile
    When the user runs "gflow image t2i 'cyberpunk city' -o custom_sub/hero.png"
    Then parent directory "custom_sub" is created if missing
    And the generated image is saved at "custom_sub/hero.png".

  Scenario: Video generation with custom output path and count greater than 1
    Given an authenticated gflow profile
    When the user runs "gflow video t2v 'waterfall in forest' --count 2 -o clip.mp4"
    Then the generated videos are saved at "clip_1.mp4" and "clip_2.mp4".

  Scenario: Cloud storage routing preserves custom relative subpath
    Given "GFLOW_CLI_STORAGE_URI" is set to "s3://my-bucket/out/"
    When an image is generated with output path "nested/render.png"
    Then the cloud storage target URI is "s3://my-bucket/out/nested/render.png".

  Scenario: Video reference-to-video (r2v) with custom output file
    Given reference images and an authenticated profile
    When the user runs "gflow video r2v 'camera pan' --ref input.png -o r2v_output.mp4"
    Then the generated video is saved at "r2v_output.mp4".

  Scenario: Video chain with custom output file
    Given a valid video chain specification
    When the user runs "gflow video chain spec.toml -o chain_output.mp4"
    Then the final chained video is saved at "chain_output.mp4".
