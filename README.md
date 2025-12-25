Automate Test Framework
Project Overview

This project is a framework-agnostic Behavior Driven Development (BDD) test automation generator.
It accepts Gherkin-based feature files and automatically generates executable test code for multiple BDD frameworks while maintaining a unified execution and output strategy.

The framework avoids dependency on framework-specific assertion mechanisms or report formats.
Instead, it extracts runtime results inside Then steps and stores them in a custom, user-defined output structure, enabling agent-based validation, retries, and cross-framework consistency.

Supported Frameworks
Framework	Language	Support Status
Behave	Python	Supported
Godog	Go	Supported
Cucumber	Java	Supported
Objectives

Automatically generate step definitions from Gherkin scenarios

Support multiple BDD frameworks using a single input format

Avoid framework-dependent reporting mechanisms

Enable custom output parsing in Then steps

Facilitate agent-driven code validation and correction

Ensure portability across environments and frameworks

Project Structure
project-root/
│
├── automation_script.py
│
├── input_files/
│   └── (user-provided feature files)
│
├── config.yaml                # User-provided configuration
│
├── knowledge_base/
│   ├── behave/
│   ├── godog/
│   └── cucumber/
│
├── requirements.txt
└── README.md


Framework-specific directories are created automatically when the script is executed.

System Requirements
General Requirements

Operating System: Windows, Linux, or macOS

Python version: 3.9 or higher

Framework-Specific Requirements
Behave

Python

behave library

Godog

Go version 1.20 or higher

godog package

Cucumber

Java version 11 or higher

Maven or Gradle

Cucumber dependencies

Installation and Setup
Clone the Repository
git clone <repository-url>
cd project-root

Install Python Dependencies
pip install -r requirements.txt

Install Framework Dependencies
Behave
pip install behave

Godog
go install github.com/cucumber/godog/cmd/godog@latest

Cucumber

Add the following dependencies using Maven or Gradle:

cucumber-java

cucumber-junit

Input Specifications
Feature File

The input feature file must follow standard Gherkin syntax.

Example
Feature: Pod health check

Scenario: Check if the pod is in Running state
  Given the mini Kube cluster is accessible
  When I check the status of the pod with label "app=flask-api"
  Then the pod status should be "Running"

Environment Configuration File

The project requires a user-provided config.yaml file.

The configuration file must contain three sections:

environment – environment details

commands – executable command templates

expected_outputs – expected results for validation

Required Structure
# config.yaml

environment:
  default_namespace: "default"

commands:
  get_minikube_status: "minikube status"
  get_pod_json_by_label: "kubectl get pod -l {label} -o json -n {namespace}"
  get_pod_stats: "kubectl get pod -l {label} -o jsonpath='{{.items[0].status.phase}}' -n {namespace}"

expected_outputs:
  minikubeStatus: "minikube\ntype: Control Plane\nhost: Running\nkubelet: Running\napiserver: Running\nkubeconfig: Configured"
  podStatusRunning: "Running"

Notes

environment contains static environment values

commands contains command templates with placeholders

expected_outputs contains expected results used during validation

Placeholders in commands must match step parameters

Execution Instructions

Run the main script:

python automation_script.py


You will be prompted for:

Path to the input feature file

Path to the environment configuration file

Target framework (behave, godog, or cucumber)

Execution Flow

Input file is validated and converted to Gherkin if required

Step definitions are generated dynamically

Framework-specific code is assembled using templates

Tests are executed using the selected framework

Runtime outputs are parsed inside Then steps

Results are written to a custom output file

Output Specification
Custom Output File

The project does not rely on framework-generated reports.
Execution results are written to a custom JSON file.

Example Output
{
  "lookup_key": "podStatus",
  "expected": "Running",
  "actual": "Running",
  "status": "PASS"
}

Output Design Principles

Then steps must not throw assertion errors

Then steps only extract and record results

Validation and decision-making occur outside the test execution layer

Agent-Based Validation

The system supports an agent-driven execution model.

The agent can:

Validate generated code

Detect compilation and runtime errors

Regenerate or correct step logic

Terminate execution once validation succeeds

This approach minimizes manual intervention and improves reliability.


Future Enhancements

Integration with external reporting tools

Web-based result visualization

Support for additional BDD frameworks like Gauge

Intended Use

This project is suitable for:

Academic submissions

Automation testing research

BDD framework comparison studies

Enterprise test automation prototyping
