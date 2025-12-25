Feature: Pod health check

  Scenario: Check if the pod is in Running state
    Given the mini Kube cluster is accessible
    When I check the status of the pod with label "app=flask-api"
    Then the pod status should be "Running"