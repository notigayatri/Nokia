Feature: User API

  Scenario: Get user by id
    Given api base url "http://localhost:5000"
    When I send a GET request to "/users/1"
    Then the response status code should be 200
    And the response should contain "Alice"