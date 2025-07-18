package main

import (
	"context"
	"fmt"
	"log"
	"strings"

	"github.com/cucumber/godog"
)

type scenarioContext struct {
	podStatus string
}

func newScenarioContext() *scenarioContext {
	return &scenarioContext{}
}

func (s *scenarioContext) givenTheMiniKubeClusterIsAccessible(ctx context.Context) error {
	// TODO: Implement step logic to check if the mini Kube cluster is accessible
	log.Println("Checking if the mini Kube cluster is accessible")
	return nil
}

func (s *scenarioContext) whenICheckTheStatusOfThePodWithLabel(ctx context.Context, label string) error {
	// TODO: Implement step logic to check the status of the pod with the given label
	log.Printf("Checking the status of the pod with label %s\n", label)
	// For demonstration purposes, assume the pod status is "Running"
	s.podStatus = "Running"
	return nil
}

func (s *scenarioContext) thenThePodStatusShouldBe(ctx context.Context, status string) error {
	// TODO: Implement step logic to check if the pod status matches the expected status
	log.Printf("Checking if the pod status is %s\n", status)
	if !strings.EqualFold(s.podStatus, status) {
		return fmt.Errorf("expected pod status to be %s, but got %s", status, s.podStatus)
	}
	return nil
}

func InitializeScenario(ctx *godog.ScenarioContext) {
	s := newScenarioContext()

	ctx.Step(`^the mini Kube cluster is accessible$`, s.givenTheMiniKubeClusterIsAccessible)
	ctx.Step(`^I check the status of the pod with label "([^"]*)"$`, s.whenICheckTheStatusOfThePodWithLabel)
	ctx.Step(`^the pod status should be "([^"]*)"$`, s.thenThePodStatusShouldBe)
}
