Feature: User API

  Scenario: Get all users
    Given the User API is running
    When I send a GET request to "/users"
    Then the response status code should be 200
    And the response should contain "Alice"
    And the response should contain "Bob"

  Scenario: Get user by id
    Given the User API is running
    When I send a GET request to "/users/1"
    Then the response status code should be 200
    And the response should contain "Alice"

  Scenario: Get non-existent user
    Given the User API is running
    When I send a GET request to "/users/999"
    Then the response status code should be 404
    And the response should contain "User not found"

  Scenario: Add a new user
    Given the User API is running
    And I have a JSON payload with name "Charlie"
    When I send a POST request to "/users" with the payload
    Then the response status code should be "201"
    And the response should contain "Charlie"
