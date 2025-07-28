package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"
)

type scenarioContext struct {
	clusterContext string
	minikubeStatus string
	podStatusJson  map[string]interface{}
	availableContext map[string]interface{}
	kubeconfigPath string
	defaultNamespace string
	label string
	resourceName string
}

func newScenarioContext() *scenarioContext {
	return &scenarioContext{}
}

func (s *scenarioContext) given_the_mini_kube_cluster_is_accessible_c7d7af66(ctx context.Context) error {
	cmd := exec.Command("minikube", "status")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to get minikube status: %w", err)
	}
	s.minikubeStatus = string(output)
	if !strings.Contains(s.minikubeStatus, "host: Running") {
		return fmt.Errorf("minikube is not running")
	}
	cmd = exec.Command("kubectl", "get", "pods", "-o", "json")
	output, err = cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("failed to get pods: %w", err)
	}
	err = json.Unmarshal(output, &s.podStatusJson)
	if err != nil {
		return fmt.Errorf("failed to unmarshal pod status json: %w", err)
	}
	s.clusterContext = "minikube"
	s.availableContext = map[string]interface{}{}
	s.kubeconfigPath = ""
	s.defaultNamespace = ""
	return nil
}

func (s *scenarioContext) when_i_check_the_status_of_the_pod_with_label_7fe71ee3(ctx context.Context, label string) error {
	s.label = label
	cmd := exec.Command("kubectl", "get", "pods", "-l", label, "-n", s.defaultNamespace, "-o", "json")
	output, err := cmd.CombinedOutput()
	if err != nil {
		return err
	}
	var podJson map[string]interface{}
	err = json.Unmarshal(output, &podJson)
	if err != nil {
		return err
	}
	s.podStatusJson = podJson
	s.clusterContext = "minikube"
	s.minikubeStatus = "Running"
	for _, pod := range s.podStatusJson["items"].([]interface{}) {
		podMap := pod.(map[string]interface{})
		if podMap["metadata"].(map[string]interface{})["labels"].(map[string]interface{})["app"] == "flask-api" {
			s.resourceName = podMap["metadata"].(map[string]interface{})["name"].(string)
			break
		}
	}
	return nil
}

func (s *scenarioContext) then_the_pod_status_should_be_240f23ab(ctx context.Context, be string) error {
	status, ok := s.podStatusJson["items"].([]interface{})[0].(map[string]interface{})["status"].(map[string]interface{})["phase"].(string)
	if !ok {
		return fmt.Errorf("failed to get pod status")
	}
	if status != be {
		return fmt.Errorf("pod status is %s, expected %s", status, be)
	}
	return nil
}

func InitializeScenario(ctx *godog.ScenarioContext) {
	s := newScenarioContext()

	ctx.Step(`^the mini Kube cluster is accessible$`, s.given_the_mini_kube_cluster_is_accessible_c7d7af66)
	ctx.Step(`^I check the status of the pod with label "([^"]*)"$`, s.when_i_check_the_status_of_the_pod_with_label_7fe71ee3)
	ctx.Step(`^the pod status should be "([^"]*)"$`, s.then_the_pod_status_should_be_240f23ab)
}