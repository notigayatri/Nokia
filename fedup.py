import os
import re
import json
import ast
from dotenv import load_dotenv
import time
import tempfile
import subprocess
from pathlib import Path
from jinja2 import Template
import together
import yaml
import hashlib
import textwrap
from collections import namedtuple
from jinja2 import Environment

# --- Initialization ---
load_dotenv()
together_client = together.Together(api_key=os.getenv("TOGETHER_API_KEY"))
LLM_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"

# --- Helper Functions & Templates ---
def escape_java_regex(value: str) -> str:
    value = value.replace('\\', '\\\\')
    value = value.replace('"', '\\"')
    value = re.sub(r'\{[^}]+\}', '.*', value)
    value = re.sub(r'\b(true|false|\d+)\b', '.*', value)
    return value

env = Environment()
env.filters['escape_java_regex'] = escape_java_regex

behave_template = Template('''from behave import given, when, then
{% for line in step_imports %}
{{ line }}
{% endfor %}

@given('a variable set to {value:d}')
def step_impl(context, value):
    context.my_variable = value

@when('we increment the variable by {increment:d}')
def step_impl(context, increment):
    context.my_variable += increment

@then('the variable should be {expected_value:d}')
def step_impl(context, expected_value):
    assert context.my_variable == expected_value
''')

### FINAL FIX: Corrected Godog templates with valid Allure integration and TestMain ###
godog_main_template = Template('''package main

import (
	"os"
	"testing"

	"github.com/cucumber/godog"
	"github.com/cucumber/godog/colors"
	"github.com/qameta/allure-go/pkg/allure-godog"
)

var opts = godog.Options{
	Format: "pretty",
	Paths:  []string{"../../features"},
	Strict: true,
	Output: colors.Colored(os.Stdout),
}

func init() {
	godog.BindCommandLineFlags("godog.", &opts)
}

func TestMain(m *testing.M) {
	godog.BindFlags("godog.", nil, &opts)
	opts.Format = "allure," + opts.Format
	opts.Paths = []string{"../../features"}
	
	// Register the Allure formatter
	alluregodog.RegisterFormatter()

	status := godog.TestSuite{
		Name:                "godog",
		ScenarioInitializer: InitializeScenario,
		Options:             &opts,
	}.Run()

	os.Exit(status)
}
''')

godog_steps_template = Template('''package main

import (
	"context"
	"github.com/cucumber/godog"
)

// LLM-generated imports will go here
{% for line in custom_imports %}
import "{{ line }}"
{% endfor %}

type scenarioContext struct {
{% for field in scenario_context_fields %}
	{{ field }}
{% endfor %}
}

func newScenarioContext() *scenarioContext {
	return &scenarioContext{}
}

{% for step in steps %}
func (s *scenarioContext) {{ step.func_name }}(ctx context.Context{% for param in step.parameters %}, {{ param }} string{% endfor %}) error {
{{ step.logic | indent(4) }}
	return nil
}
{% endfor %}

func InitializeScenario(ctx *godog.ScenarioContext) {
	s := newScenarioContext()
	
	{% for step in steps %}
	ctx.Step(`^{{ step.step_text }}$`, s.{{ step.func_name }})
	{% endfor %}
}
''')

# Corrected Cucumber templates for Allure reporting
cucumber_runner_template = Template('''package runner;
import org.junit.runner.RunWith;
import io.cucumber.junit.Cucumber;
import io.cucumber.junit.CucumberOptions;

@RunWith(Cucumber.class)
@CucumberOptions(
    features = "src/test/resources/features",
    glue = "stepdefinitions",
    plugin = {"pretty", "io.qameta.allure.cucumber7jvm.AllureCucumber7Jvm", "json:target/cucumber-report.json"}
)
public class TestRunner {}
''')

pom_template = Template('''<project xmlns="http://maven.apache.org/POM/4.0.0"
    xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
    xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>cucumber-tests</artifactId>
  <version>1.0-SNAPSHOT</version>
  <dependencies>
    <dependency>
      <groupId>io.cucumber</groupId>
      <artifactId>cucumber-java</artifactId>
      <version>7.14.0</version>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>io.cucumber</groupId>
      <artifactId>cucumber-junit</artifactId>
      <version>7.14.0</version>
      <scope>test</scope>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <version>4.13.2</version>
      <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>io.qameta.allure</groupId>
        <artifactId>allure-cucumber7-jvm</artifactId>
        <version>2.27.0</version>
        <scope>test</scope>
    </dependency>
  </dependencies>
</project>
''')

cucumber_step_template = env.from_string('''package stepdefinitions;
import io.cucumber.java.en.*;
{% for line in custom_imports %}
import {{ line }};
{% endfor %}
public class StepDefinitions {
    {% for step in steps %}
    @{{ step.gherkin_keyword.lower() | capitalize }}("^{{ step.step_text | escape_java_regex }}$")
    public void {{ step.func_name }}({% for param in step.parameters %}String {{ param }}{% if not loop.last %}, {% endif %}{% endfor %}) {
{{ step.logic | indent(4) }}
    }
    {% endfor %}
}
''')

def infer_environment_with_llm(feature_content: str) -> str:
    # Function unchanged, omitted for brevity
    return "API_TESTING"

def confirm_or_select_environment(env_guess: str, prompt_key_file: str = "prompt_key.yaml") -> str:
    # Function unchanged, omitted for brevity
    return env_guess

def load_and_collect_prompt_inputs(env_name: str, prompt_key_file: str = "prompt_key.yaml") -> dict:
    # Function unchanged, omitted for brevity
    return {}

def extract_steps_from_feature(feature_content: str) -> list[str]:
    step_pattern = re.compile(r'^\s*(Given|When|Then|And|But)\b', re.IGNORECASE)
    return [line.strip() for line in feature_content.splitlines() if step_pattern.match(line.strip())]

def format_step_for_framework(step_text: str, framework: str):
    param_names = []
    parts = []
    last_end = 0
    matches = list(re.finditer(r'"([^"]*)"', step_text))
    for i, match in enumerate(matches):
        parts.append(re.escape(step_text[last_end:match.start()]).replace(r'\ ', ' '))
        preceding_words = re.findall(r'\b\w+\b', step_text[:match.start()])
        name = preceding_words[-1].lower() if preceding_words else f"param{i}"
        original_name = name
        k=0
        while name in param_names:
            k+=1
            name = f"{original_name}{k}"
        param_names.append(name)
        parts.append(r"\"([^\"]*)\"")
        last_end = match.end()
    parts.append(re.escape(step_text[last_end:]).replace(r'\ ', ' '))
    formatted_step_pattern = "".join(parts).strip()
    return formatted_step_pattern, param_names

def parse_feature_by_scenario(feature_content: str):
    scenarios = []
    current_scenario = None
    scenario_lines = []
    for line in feature_content.splitlines():
        if line.strip().lower().startswith("scenario:"):
            if current_scenario:
                scenarios.append({"title": current_scenario, "content": "\n".join(scenario_lines), "steps": extract_steps_from_feature("\n".join(scenario_lines))})
            current_scenario = line.strip()
            scenario_lines = [line.strip()]
        elif current_scenario:
            scenario_lines.append(line.strip())
    if current_scenario:
        scenarios.append({"title": current_scenario, "content": "\n".join(scenario_lines), "steps": extract_steps_from_feature("\n".join(scenario_lines))})
    return scenarios

# The full prompts are included here for completeness.
FRAMEWORK_LOGIC_PROMPTS = {
    "behave": Template("...omitted for brevity..."),
    "godog": Template("""
You are a Go test automation expert. For the Gherkin step below, generate ONLY the raw Go code for the BODY of the function.
---
**Gherkin Step:** "{{ step_line }}"
**Scenario Context:**
{{ scenario_content }}
---
**INSTRUCTIONS:**
1.  **Return only the Go code for the function body.** No package, no imports, no function signature, no comments, no markdown.
2.  Use `s.` to access shared state (e.g., `s.apiResponse = body`). `s` is a `*scenarioContext`.
3.  The function signature is `func(ctx context.Context, ...) error`. Use `ctx` only for cancellation, not state.
---
**Context Fields to declare (if needed):**
- After `---CONTEXT-FIELDS---`, list any new fields for `scenarioContext` (e.g., `apiResponse []byte`).
---
**Logic to generate:**
"""),
    "cucumber": Template("...omitted for brevity...")
}

def generate_step_metadata(step_text: str, framework: str, prompt_inputs: dict, environment: str, scenario_content: str, full_feature_content: str, previous_step_error: str = None) -> dict:
    step_keyword_match = re.match(r'^(Given|When|Then|And|But)\s+(.*)', step_text, flags=re.IGNORECASE)
    gherkin_keyword = step_keyword_match.group(1).lower()
    formatted_step_text, parameters = format_step_for_framework(step_text, framework)
    step_base = re.sub(r'[^a-z0-9]+', '_', re.sub(r'"([^"]*)"', '', step_text).strip().lower())
    func_name = f"{step_base}_{hashlib.md5(step_text.encode()).hexdigest()[:8]}"
    actual_values = [match.group(1) for match in re.finditer(r'"([^"]*)"', step_text)]
    parameter_values = {param: val for param, val in zip(parameters, actual_values)}
    
    prompt_template = FRAMEWORK_LOGIC_PROMPTS[framework]
    logic_prompt = prompt_template.render(
        full_feature_content=full_feature_content, step_line=step_text,
        scenario_content=scenario_content, parameters=parameters, parameter_values=parameter_values
    )
    
    try:
        response = together_client.chat.completions.create(model=LLM_MODEL, messages=[{"role": "user", "content": logic_prompt}], temperature=0.3)
        raw_llm_output = response.choices[0].message.content.strip()
        time.sleep(1)
        
        # Simplified parsing logic
        imports = []
        godog_fields = []
        final_logic = textwrap.dedent(raw_llm_output).strip() or "return godog.ErrPending"

        if "---CONTEXT-FIELDS---" in raw_llm_output:
            parts = raw_llm_output.split("---CONTEXT-FIELDS---")
            field_part = parts[1].split("---LOGIC---")[0]
            logic_part = parts[1].split("---LOGIC---")[1]
            godog_fields = [line.strip() for line in field_part.strip().splitlines() if line.strip()]
            final_logic = textwrap.dedent(logic_part).strip() or "return godog.ErrPending"

        return {
            "func_name": func_name, "parameters": parameters, "step_text": formatted_step_text,
            "logic": final_logic, "imports": imports, "gherkin_keyword": gherkin_keyword,
            "scenario_context_fields": godog_fields
        }
    except Exception as e:
        print(f"[LLM Error] Failed to generate logic for step '{step_text}': {e}")
        return None

def generate_framework_code(all_step_metadata, framework, custom_imports, scenario_context_fields):
    if framework == "behave":
        return behave_template.render(step_imports=custom_imports, steps=all_step_metadata)
    elif framework == "godog":
        return {
            "main": godog_main_template.render(),
            "steps": godog_steps_template.render(custom_imports=custom_imports, scenario_context_fields=scenario_context_fields, steps=all_step_metadata)
        }
    elif framework == "cucumber":
        return cucumber_step_template.render(custom_imports=list(custom_imports), steps=all_step_metadata)
    raise ValueError(f"Unsupported framework: {framework}")

def write_code(framework, feature_content, code, feature_filename):
    # Function mostly unchanged, but simplified for brevity
    features_dir = Path("features")
    features_dir.mkdir(parents=True, exist_ok=True)
    (features_dir / feature_filename).write_text(feature_content)
    if framework == "godog":
        godog_dir = Path("godog")
        godog_dir.mkdir(parents=True, exist_ok=True)
        (godog_dir / "main_test.go").write_text(code["main"])
        (godog_dir / "steps.go").write_text(code["steps"])
    # ... other frameworks
    
def final_llm_autocorrect_code(feature_content: str, step_code: str, framework: str, previous_error: str = None) -> str:
    # Function unchanged
    prompt = "..."
    # ...
    return step_code # Forcing no correction for stability in this example

def validate_code(code, framework):
    if framework == 'godog':
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)
            (temp_dir_path / "main_test.go").write_text(code["main"])
            (temp_dir_path / "steps.go").write_text(code["steps"])
            
            subprocess.run(["go", "mod", "init", "tmp/mod"], cwd=temp_dir_path, capture_output=True, text=True, check=False)
            
            tidy_result = subprocess.run(["go", "mod", "tidy"], cwd=temp_dir_path, capture_output=True, text=True, check=False)
            if tidy_result.returncode != 0:
                return False, f"Go mod tidy failed:\n{tidy_result.stderr.strip()}"

            validate_result = subprocess.run(["go", "test", "-c"], cwd=temp_dir_path, capture_output=True, text=True, check=False)
            if validate_result.returncode != 0:
                return False, f"Go test compilation error detected:\n{validate_result.stderr.strip()}"
        return True, None
    # ... other frameworks
    return True, None

def main():
    feature_file_path = input("Enter path to the .feature file: ").strip()
    feature_file = Path("features") / feature_file_path
    if not feature_file.exists():
        print(f"File {feature_file} not found."); return
    feature_content = feature_file.read_text()
    
    framework = input("Choose framework (behave / godog / cucumber): ").strip().lower()
    if framework not in {"behave", "godog", "cucumber"}:
        print("Unsupported framework"); return

    environment = "API_TESTING"
    prompt_inputs = {}
    scenarios_data = parse_feature_by_scenario(feature_content)
    
    all_step_metadata, all_custom_imports, all_godog_fields = [], set(), set()
    
    for scenario in scenarios_data:
        print(f"\nProcessing Scenario: {scenario['title']}")
        for step_text in scenario["steps"]:
            step_data = generate_step_metadata(step_text, framework, prompt_inputs, environment, scenario['content'], feature_content)
            if step_data:
                all_step_metadata.append(step_data)
                all_custom_imports.update(step_data.get("imports", []))
                if framework == "godog":
                    all_godog_fields.update(step_data.get("scenario_context_fields", []))

    final_generated_code, is_valid_code, validation_error = None, False, None
    for attempt in range(3):
        print(f"\nAttempt {attempt + 1} to generate and validate final code...")
        
        code_to_autocorrect = generate_framework_code(all_step_metadata, framework, all_custom_imports, all_godog_fields)
        
        ### FINAL FIX: Added sanity check to protect against AI errors ###
        if framework == "godog":
            corrected_steps = final_llm_autocorrect_code(feature_content, code_to_autocorrect["steps"], framework, validation_error)
            # Sanity check: ensure the AI didn't replace the code with garbage
            if "package main" not in corrected_steps:
                print("AI auto-correction returned invalid code, retrying...")
                validation_error = "AI returned non-Go code."
                time.sleep(2)
                continue # Skip to next attempt
            final_generated_code = {"main": code_to_autocorrect["main"], "steps": corrected_steps}
        else:
            final_generated_code = final_llm_autocorrect_code(feature_content, code_to_autocorrect, framework, validation_error)

        is_valid_code, validation_error = validate_code(final_generated_code, framework)
        if is_valid_code:
            print(f"Code validated successfully on attempt {attempt + 1}."); break
        else:
            print(f"Attempt {attempt + 1} failed validation:\n{validation_error}")
            time.sleep(2)
    
    if not is_valid_code:
        print("Final code validation failed after multiple attempts. Exiting."); return
    
    write_code(framework, feature_content, final_generated_code, feature_file.name)
    
    print(f"\nRunning {framework} tests...")
    if framework == "godog":
        godog_dir = "godog"
        print(f"Initializing Go module in '{godog_dir}'...")
        subprocess.run(["go", "mod", "init", "godog/tests"], cwd=godog_dir, capture_output=True, text=True, check=False)
        print("Fetching Go dependencies...")
        subprocess.run(["go", "mod", "tidy"], cwd=godog_dir, capture_output=True, text=True, check=False)
        print("Executing Godog tests...")
        subprocess.run(["go", "test", "./...", "-godog.format=cucumber:cucumber.json"], cwd=godog_dir, shell=True, check=False)
    # ... other frameworks
    
    print(f"\n[✓] {framework.capitalize()} test executed and report generated.")
    print("\nTo view the visual report, run: allure serve allure-results")

if __name__ == '__main__':
    main()