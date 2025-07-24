Feature: Calculator Addition

  Scenario: Add two numbers
    Given the first number is 5
    And the second number is 7
    When the calculator adds the numbers
    Then the result should be 12
