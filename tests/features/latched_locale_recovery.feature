Feature: A latched profile can still learn its locale
  As an operator whose profile cached NOT_REDIRECTED before the rollout
  I want the account locale recovered from the document
  So that a cache about redirects does not permanently disable locale resolution

  Scenario: the cached state says the account is not redirected
    Given a profile cached as NOT_REDIRECTED
    And Flow renders the document with lang "pt"
    When gflow bootstraps
    Then the account locale resolves to "pt"
    And no URL settle is awaited
    And the profile stays cached as NOT_REDIRECTED

  Scenario: the document declares no usable locale
    Given a profile cached as NOT_REDIRECTED
    And Flow renders the document with no lang attribute
    When gflow bootstraps
    Then the account locale stays unresolved
    And the profile stays cached as NOT_REDIRECTED
