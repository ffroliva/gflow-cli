Feature: models Flow lists as leaving are flagged before they go (#1375)
  Flow labels Omni 1.1 Flash, Veo 3.1 - Fast and Veo 3.1 - Quality "Leaving 5 Dec" and
  carries the date in its model catalog (HTrJv). gflow shows the date, warns when one of
  these models is selected, and never refuses a request on the date alone: Flow's own menu
  decides whether a model is still offered.

  Scenario: the model catalog shows when a model leaves
    When the model catalog is built
    Then Veo 3.1 Fast, Veo 3.1 Quality and Omni 1.1 Flash carry retires_on 2026-12-05
    And every other video model carries retires_on null

  Scenario: selecting a leaving model warns without blocking
    Given today is 2026-12-04
    When a generation request selects Veo 3.1 Fast
    Then a "model.retiring" warning names veo_3_1_fast and 2026-12-05
    And the request is not refused

  Scenario: a model past its date still is not refused on the date alone
    Given today is 2026-12-06
    When a generation request selects Veo 3.1 Fast
    Then a "model.retiring" warning names veo_3_1_fast and 2026-12-05
    And the request is not refused

  Scenario: a model Flow is not retiring gets no warning
    Given today is 2026-12-04
    When a generation request selects Veo 3.1 Lite
    Then no "model.retiring" warning is emitted

  Scenario: the recorded Flow catalog agrees with gflow's static dates
    Given the recorded HTrJv catalog
    Then every gflow video model maps to a family key present in it
    And each family's retirement date equals gflow's retires_on
    And the catalog shows no drift from gflow's model mapping

  Scenario: a family Flow adds is reported as drift (#1550)
    Given the recorded HTrJv catalog with a new family "veo_4"
    Then the catalog drift names "added family veo_4"
