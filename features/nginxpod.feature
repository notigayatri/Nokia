Feature: Verify NGINX deployment on Kubernetes

  Scenario: Check if NGINX service is accessible
    Given the NGINX service is deployed in the Kubernetes cluster
    When I send a GET request to the NGINX NodePort URL
    Then I should receive a 200 OK response
    And the response should contain "Welcome to nginx!"
