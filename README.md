Automate Test Framework
1. Project Overview

This project is a framework-agnostic Behavior Driven Development (BDD) test automation generator. It accepts Gherkin-based feature files and automatically generates executable test code for multiple BDD frameworks while maintaining a unified execution and output strategy.

The system supports the following frameworks:

Behave (Python)

Godog (Go)

Cucumber (Java)

Instead of relying on framework-specific assertion mechanisms or report formats, the project focuses on extracting runtime results and storing them in a custom, user-defined output structure. This design enables agent-based validation, retry logic, and cross-framework consistency.

2. Objectives

Automatically generate step definitions from Gherkin scenarios

Support multiple BDD frameworks using a single input format

Avoid framework-dependent reporting mechanisms

Enable custom output parsing in Then steps

Facilitate agent-driven code validation and correction

Ensure portability across environments and frameworks

3. Supported Frameworks
Framework	Language	Support Status
Behave	Python	Supported
Godog	Go	Supported
Cucumber	Java	Supported
4. Project Structure
project-root/
│
├── automation_script.py
│
├── input_files/
│   └── user_provided_feature_files
│
├── config.yaml
│
├── knowledge_base/
│    ├── behave/
│    ├── godog/
│    └── cucumber/
│
├── requirements.txt
├── README.md


5. System Requirements
5.1 General Requirements

Operating System: Windows, Linux, or macOS

Python version 3.9 or higher

5.2 Framework-Specific Requirements
Behave

Python

Behave library

Godog

Go version 1.20 or higher

Godog package

Cucumber

Java version 11 or higher

Maven or Gradle

Cucumber dependencies

6. Installation and Setup
6.1 Clone the Repository
git clone <repository-url>
cd project-root

6.2 Install Python Dependencies
pip install -r requirements.txt

6.3 Install Framework Dependencies
Behave
pip install behave

Godog
go install github.com/cucumber/godog/cmd/godog@latest

Cucumber

Add the following dependencies using Maven or Gradle:

cucumber-java

cucumber-junit

7. Input Specifications
7.1 Feature File

The input feature file must follow standard Gherkin syntax.

Example:

Feature: Pod health check

  Scenario: Check if the pod is in Running state
    Given the mini Kube cluster is accessible
    When I check the status of the pod with label "app=flask-api"
    Then the pod status should be "Running"

7.2 Environment Configuration File

The project requires a user-provided yaml file.

The configuration file must contain three sections:

environment – environment details

commands – executable command templates

expected_outputs – expected results for validation

Required Structure
# config.yaml

# Environment details
environment:
  default_namespace: "default"

# Command templates
commands:
  get_minikube_status: "minikube status"
  get_pod_json_by_label: "kubectl get pod -l {label} -o json -n {namespace}"
  get_pod_stats: "kubectl get pod -l {label} -o jsonpath='{{.items[0].status.phase}}' -n {namespace}"

# Expected outputs
expected_outputs:
  minikubeStatus: "minikube\ntype: Control Plane\nhost: Running\nkubelet: Running\napiserver: Running\nkubeconfig: Configured"
  podStatusRunning: "Running"

Notes

environment contains static environment values

commands contains command templates with placeholders

expected_outputs contains expected results used during validation

Placeholders in commands must match step parameters

This configuration file is loaded at runtime and can be accessed within step logic.

8. Execution Instructions

Run the main entry point:

python main.py


The system will prompt for:

Path to the input feature file

Path to the environment configuration file

Target framework (behave, godog, or cucumber)

9. Execution Flow

The input file is validated and converted to Gherkin format if required.

Step definitions are generated dynamically.

Framework-specific code is assembled using templates.

Tests are executed using the selected framework.

Runtime outputs are parsed inside Then steps.

Results are written to a custom output file.

10. Output Specification
10.1 Custom Output File

The project does not rely on framework-generated reports. Instead, execution results are written to a custom JSON file.

Example output:

{
  "lookup_key": "podStatus",
  "expected": "Running",
  "actual": "Running",
  "status": "PASS"
}

10.2 Output Design Principle

Then steps must not throw assertion errors.

Then steps are responsible only for extracting and recording results.

Validation and decision-making occur outside the test execution layer.

11. Agent-Based Validation

The system is designed to work with an agent-driven execution model. The agent can:

Validate generated code

Detect runtime and compilation errors

Regenerate or correct step logic

Terminate execution when validation succeeds

This approach minimizes manual intervention and improves reliability.

12. Limitations

Godog requires careful handling of errors in Then steps to avoid runtime crashes.

Output files are overwritten per execution unless explicitly versioned.

Parallel execution is not enabled by default.

13. Future Enhancements

Unified result aggregation across scenarios

Retry mechanism for failed steps

Integration with external reporting tools

Web-based result visualization

Support for additional BDD frameworks

14. Intended Use

This project is suitable for:

Academic submissions

Automation testing research

BDD framework comparison studies

Enterprise test automation prototyping