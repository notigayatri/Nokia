# Automate Test Framework

## Project Overview

This project is a framework-agnostic Behavior Driven Development (BDD) test automation generator.
It accepts Gherkin-based feature files and automatically generates executable test code for multiple
BDD frameworks while maintaining a unified execution and output strategy.

The framework avoids framework-specific assertion mechanisms and report formats.
Instead, runtime results are extracted inside Then steps and stored in a custom,
user-defined output structure to support agent-based validation and retries.

## Supported Frameworks

* Behave (Python)
* Godog (Go)
* Cucumber (Java)

## Objectives

* Automatically generate step definitions from Gherkin scenarios
* Support multiple BDD frameworks using a single input format
* Avoid framework-dependent reporting mechanisms
* Enable custom output parsing in Then steps
* Facilitate agent-driven code validation and correction
* Ensure portability across environments and frameworks

## Project Structure

```
project-root/
├── automation_script.py
├── input_files/
│   └── (user-provided feature files)
├── config.yaml
├── knowledge_base/
│   ├── behave/
│   ├── godog/
│   └── cucumber/
├── requirements.txt
└── README.md
```

Framework-specific directories are created automatically when the script is executed.

## System Requirements

### General Requirements

* Operating System: Windows, Linux, or macOS
* Python 3.9 or higher

### Framework-Specific Requirements

#### Behave

* Python
* behave library

#### Godog

* Go 1.20 or higher
* godog package

#### Cucumber

* Java 11 or higher
* Maven or Gradle
* Cucumber dependencies

## Installation and Setup

### Clone the Repository

```
git clone <repository-url>
cd project-root
```

### Install Python Dependencies

```
pip install -r requirements.txt
```

### Install Framework Dependencies

#### Behave

```
pip install behave
```

#### Godog

```
go install github.com/cucumber/godog/cmd/godog@latest
```

#### Cucumber

Add the following dependencies using Maven or Gradle:

* cucumber-java
* cucumber-junit

## Input Specifications

### Feature File

The input feature file must follow standard Gherkin syntax.

Example:

```
Feature: Pod health check

Scenario: Check if the pod is in Running state
  Given the mini Kube cluster is accessible
  When I check the status of the pod with label "app=flask-api"
  Then the pod status should be "Running"
```

### Configuration File

A user-provided YAML configuration file is required.

The configuration file must contain:

* environment details
* command templates
* expected outputs

### Required Structure

```
environment:
  default_namespace: "default"

commands:
  get_minikube_status: "minikube status"
  get_pod_json_by_label: "kubectl get pod -l {label} -o json -n {namespace}"
  get_pod_stats: "kubectl get pod -l {label} -o jsonpath='{{.items[0].status.phase}}' -n {namespace}"

expected_outputs:
  minikubeStatus: "minikube\ntype: Control Plane\nhost: Running\nkubelet: Running\napiserver: Running\nkubeconfig: Configured"
  podStatusRunning: "Running"
```

Notes:

* environment contains static values
* commands contain templates with placeholders
* expected_outputs contain expected values for validation
* placeholders must match step parameters

## Execution Instructions

Run the main script:

```
python automation_script.py
```

You will be prompted for:

* Input feature file path
* Configuration file path
* Target framework (behave, godog, cucumber)

## Execution Flow

1. Input file is validated or converted to Gherkin
2. Step definitions are generated
3. Framework-specific code is assembled
4. Tests are executed
5. Runtime values are extracted in Then steps
6. Results are written to a custom output file

## Output Specification

Framework-generated reports are not used.

Results are written to a custom JSON file.

Example:

```
{
  "lookup_key": "podStatus",
  "expected": "Running",
  "actual": "Running",
  "status": "PASS"
}
```

Design principles:

* Then steps do not assert
* Then steps only extract and record values
* Validation occurs outside test execution

## Agent-Based Validation

An agent validates generated code by:

* Detecting compilation and runtime errors
* Regenerating or correcting step logic
* Stopping execution once validation succeeds

## Future Enhancements

* Integration with external reporting tools (Allure, dashboards)
* Web-based visualization of custom output results
* Support for additional BDD frameworks like Gauge

## Intended Use

This project is suitable for:

* Academic submissions
* Automation testing research
* BDD framework comparison studies
* Enterprise test automation prototyping
