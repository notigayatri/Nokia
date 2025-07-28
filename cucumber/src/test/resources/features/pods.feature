Feature: Verify operational health of deployed application pods
  The system should be able to verify the operational health of deployed application pods within the Kubernetes cluster.

  Scenario: Verify a specific application pod is running correctly
    Given the mini Kube cluster is up and reachable
    When I request the current operational status of the pod with label "app=flask-api"
    Then the returned status for this pod must be "Running"