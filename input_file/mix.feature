Feature: Mix testing

  Scenario: Get all users
    Given the User API is running
    When I send a GET request to "/users"
    Then the response status code should be 200
    And the response should contain "Alice"
    And the response should contain "Bob"

  Scenario: Check if the pod is in Running state
    Given the mini Kube cluster is accessible
    When I check the status of the pod with label "app=flask-api"
    Then the pod status should be "Running"

 