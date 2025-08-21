Feature: User API

  Scenario Outline: Get user by ID
    Given api base url "http://localhost:5000"
    When I send a GET request to "/users/<user_id>"
    Then the response status code should be 200
    And the response should contain "<user_name>"

  Examples:
    | user_id | user_name |
    | 1       | Alice     |
    | 2       | Bob       |