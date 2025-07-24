package main

import (
	"github.com/cucumber/godog"
	"fmt"
)

type scenarioContext struct {
	expectedResult int
	firstNumber    int
	result         int
	secondNumber   int
}

func newScenarioContext() *scenarioContext {
	return &scenarioContext{}
}

func (s *scenarioContext) givenTheFirstNumberIs(arg1 int) error {
	s.firstNumber = arg1
	fmt.Printf("First number set to: %d\n", s.firstNumber)
	return nil
}

func (s *scenarioContext) andTheSecondNumberIs(arg1 int) error {
	s.secondNumber = arg1
	return nil
}

func (s *scenarioContext) whenTheCalculatorAddsTheNumbers() error {
	s.result = s.firstNumber + s.secondNumber
	return nil
}

func (s *scenarioContext) thenTheResultShouldBe(arg1 int) error {
	s.expectedResult = arg1
	if s.result != s.expectedResult {
		return fmt.Errorf("result mismatch: expected %d, got %d", s.expectedResult, s.result)
	}
	return nil
}

func InitializeScenario(ctx *godog.ScenarioContext) {
	s := newScenarioContext()

	ctx.Step(`^the first number is (\d+)$`, s.givenTheFirstNumberIs)
	ctx.Step(`^the second number is (\d+)$`, s.andTheSecondNumberIs)
	ctx.Step(`^the calculator adds the numbers$`, s.whenTheCalculatorAddsTheNumbers)
	ctx.Step(`^the result should be (\d+)$`, s.thenTheResultShouldBe)
}