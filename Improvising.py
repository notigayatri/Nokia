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
load_dotenv() # Load environment variables from .env file
# Initialize Together AI client
together_client = together.Together(api_key=os.getenv("TOGETHER_API_KEY"))
LLM_MODEL="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"

def escape_java_regex(value: str) -> str:
    value = value.replace('\\', '\\\\')      # escape backslashes
    value = value.replace('"', '\\"')        # escape double quotes
    value = re.sub(r'\{[^}]+\}', '.*', value) # replace {params} with .*
    value = re.sub(r'\b(true|false|\d+)\b', '.*', value) # match literals
    return value

env = Environment()
env.filters['escape_java_regex'] = escape_java_regex

behave_template = Template('''from behave import given, when, then
{% for line in step_imports %}
{{ line }}
{% endfor %}

{% for step in steps %}
@{{ step.gherkin_keyword.lower() }}('{{ step.step_text }}')
def {{ step.func_name }}(context{% for param in step.parameters %}, {{ param }}{% endfor %}):
    {{ step.logic | indent(4) }}
{% endfor %}
''')

# --- FIX 1: Modified godog_template ---
# Added non-negotiable, static imports for `context` and `godog`
# The LLM will now only provide the *additional* imports.
godog_template = Template('''package main

import (
    "context"
    "github.com/cucumber/godog"
)

// LLM-generated imports will go here
{% for line in custom_imports %}
import "{{ line }}"
{% endfor %}

// LLM-generated scenarioContext struct will go here
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


cucumber_step_template = env.from_string('''package stepdefinitions;
// Custom imports for step definitions
{% for line in custom_imports %}
import {{ line }};
{% endfor %}

public class StepDefinitions {

    {% for step in steps %}
    @{{ step.gherkin_keyword.lower() | capitalize }}("^{{ step.step_text | escape_java_regex }}$")
    public void {{ step.func_name }}({% if step.parameters %}{% for param in step.parameters %}String {{ param }}{% if not loop.last %}, {% endif %}{% endfor %}{% endif %}) {
{{ step.logic | indent(4) }}
    }
    {% endfor %}
}
''')

cucumber_runner_template = Template('''package runner;

import org.junit.runner.RunWith;
import io.cucumber.junit.Cucumber;
import io.cucumber.junit.CucumberOptions;

@RunWith(Cucumber.class)
@CucumberOptions(
    features = "src/test/resources/features",
    glue = "stepdefinitions",
    plugin = {"json:target/cucumber-report.json", "pretty"}
)
public class TestRunner {
}
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
  </dependencies>
</project>
''')


StepBody = namedtuple("StepBody", ["params", "code"])

def infer_environment_with_llm(feature_content: str) -> str:
    print("\nInferring the environment type from the feature file...")

    prompt = f"""
You are an expert test automation analyst.

Below is a Gherkin feature file. Based on its content, predict the environment where this test belongs.
Possible environments:
- WEB_UI_TESTING
- API_TESTING
- CLI_TESTING
- DATABASE_TESTING
- KUBERNETES_TESTING
- GENERAL_TESTING

Respond ONLY with the environment name, nothing else.

Feature file:
---
{feature_content}
---
"""

    try:
        response = together_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        env_guess = response.choices[0].message.content.strip().upper()
        time.sleep(1)

        print(f"Inferred Environment: {env_guess}")
        return env_guess
    except Exception as e:
        print(f"LLM failed to infer environment: {e}")
        return ""

def confirm_or_select_environment(env_guess: str, prompt_key_file: str = "prompt_key.yaml") -> str:
    print(f"LLM guessed the environment as: {env_guess}")
    confirm = input("Do you want to use this environment? (yes/no): ").strip().lower()

    if confirm in ["yes", "y"]:
        return env_guess

    # Else, user provides their own environment
    user_env = input("Enter the correct environment name: ").strip().upper()

    # Load YAML
    with open(prompt_key_file, "r") as f:
        prompt_data = yaml.safe_load(f) or {}

    # If the new environment is not present, generate prompt keys using LLM
    if user_env not in prompt_data:
        print(f"Environment '{user_env}' not found in prompt_key.yaml. Generating prompt keys via LLM...")

        prompt = f"""
You are a test automation architect.

A new environment '{user_env}' has been introduced. Please generate a list of prompt keys needed for this environment in the following YAML-compatible format:

Each key should include:
- key
- description
- type (string, integer, boolean, enum, json_string, file_path, key_value_pairs)
- example
- options (only if type is enum)

Return the YAML for: {user_env}
Only return valid YAML.
"""
        try:
            response = together_client.chat.completions.create(
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3
            )
            new_yaml_str = response.choices[0].message.content
            time.sleep(1)

            # Load newly generated keys from LLM output
            new_env_data = yaml.safe_load(new_yaml_str)

            # Merge into existing YAML
            prompt_data.update(new_env_data)

            # Save back to YAML
            with open(prompt_key_file, "w") as f:
                yaml.dump(prompt_data, f)

            print(f"Prompt keys for '{user_env}' added to {prompt_key_file}.")

        except Exception as e:
            print(f"Failed to generate or write prompt keys: {e}")
            return user_env  # Proceed, but assume keys might be empty

    return user_env

def load_and_collect_prompt_inputs(env_name: str, prompt_key_file: str = "prompt_key.yaml") -> dict:
    print(f"\nLoading prompt keys for environment: {env_name}")

    with open(prompt_key_file, "r") as f:
        prompt_data = yaml.safe_load(f)

    if env_name not in prompt_data:
        print(f"No prompt keys found for environment: {env_name}")
        return {}

    user_inputs = {}

    for item in prompt_data[env_name]:
        key = item.get("key")
        desc = item.get("description", "")
        example = item.get("example", "")
        options = item.get("options", [])
        type_ = item.get("type", "string")

        print(f"\n🔹 {desc}")
        if options:
            print(f"Options: {options}")
        print(f"Example: {example}")

        user_input = input(f"Enter value for `{key}` (or type `0` to skip): ").strip()

        if user_input.lower() == "0":
            continue

        user_inputs[key] = user_input

    # NEW: Add additional free-form input
    extra_info = input("\nEnter any additional environment instructions (e.g., 'Minikube is already running. Do not check env variables'): ").strip()
    if extra_info:
        user_inputs["__additional_instructions__"] = extra_info

    print(f"\nCollected {len(user_inputs)} prompt inputs.")
    return user_inputs


def extract_steps_from_feature(feature_content: str) -> list[str]:
    """
    Extracts Gherkin step lines (Given, When, Then, And, But) from feature content.
    Ignores comments, Scenario/Feature declarations, and whitespace.
    """
    step_pattern = re.compile(r'^\s*(Given|When|Then|And|But)\b', re.IGNORECASE)
    step_lines = []

    for line in feature_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("feature:") or stripped.lower().startswith("scenario:"):
            continue
        if step_pattern.match(stripped):
            step_lines.append(stripped)

    return step_lines

def format_step_for_framework(step_text: str, framework: str):
    param_names = []
    parts = []
    last_end = 0

    matches = list(re.finditer(r'"([^"]*)"', step_text))

    for i, match in enumerate(matches):
        static_part = step_text[last_end:match.start()]
        if framework == "cucumber" or framework == "godog":
            static_part = re.escape(static_part).replace(r'\ ', ' ')
        parts.append(static_part)

        preceding_text = step_text[:match.start()]
        preceding_words = re.findall(r'\b\w+\b', preceding_text)
        if preceding_words:
            name = preceding_words[-1].lower()
            original_name = name
            k = 0
            while name in param_names:
                k += 1
                name = f"{original_name}{k}"
        else:
            name = f"param{i}"
            
        param_names.append(name)

        if framework == "behave":
            parts.append(f"{{{name}}}")
        else:
            parts.append(r"\"([^\"]*)\"")
        
        last_end = match.end()

    remaining_part = step_text[last_end:]
    if framework == "cucumber" or framework == "godog":
        remaining_part = re.escape(remaining_part).replace(r'\ ', ' ')
    parts.append(remaining_part)

    formatted_step_pattern = "".join(parts).strip()
    
    return formatted_step_pattern, param_names

def parse_feature_by_scenario(feature_content: str):
    scenarios = []
    current_scenario = None
    scenario_lines = []
    
    for line in feature_content.splitlines():
        if line.strip().lower().startswith("scenario:"):
            if current_scenario:
                scenarios.append({
                    "title": current_scenario,
                    "content": "\n".join(scenario_lines),
                    "steps": extract_steps_from_feature("\n".join(scenario_lines))
                })
            current_scenario = line.strip()
            scenario_lines = [line.strip()]
        elif current_scenario:
            scenario_lines.append(line.strip())
            
    if current_scenario: # Add the last scenario
        scenarios.append({
            "title": current_scenario,
            "content": "\n".join(scenario_lines),
            "steps": extract_steps_from_feature("\n".join(scenario_lines))
        })
    return scenarios

FRAMEWORK_LOGIC_PROMPTS = {
    "behave": Template("""
You are an expert test automation engineer. Your task is to implement the body of a test function for a given Gherkin step, considering the context of the entire scenario and the full feature.

**Full Gherkin Feature:**
---
{{ full_feature_content }}
---

**Current Gherkin Step to implement:** "{{ step_line }}"
**Full Gherkin Scenario:**
---
{{ scenario_content }}
---

Framework: Behave (Python)
Environment: {{ environment }}
Parameters for this function: {{ parameters }}
Parameter values: {{ parameter_values }}
Contextual details provided by user:
{{ prompt_inputs }}

---
**IMPORTANT INSTRUCTIONS FOR YOUR RESPONSE:**
1.  **GENERATE ONLY THE CODE FOR THE FUNCTION BODY.** Do NOT include the function definition (e.g., `def func_name(...)`), decorators (e.g., `@given(...)`), or any surrounding boilerplate.
2.  **DO NOT wrap the code in markdown code blocks (e.g., ```python).** Provide raw code.
3.  **DO NOT include any comments, explanations, or extra text.** Just the executable code.
4.  **DO NOT define helper functions within this function.**
5.  Use the exact parameter names provided: {{ parameters }} in the steps instead of creating new variables.
6.  Ensure all necessary imports for your code are explicitly stated at the very top of your generated body, e.g., `import requests` or `from selenium import webdriver`.
7.  **Crucially, use the `context` object exclusively for storing and retrieving *actual runtime data* that flows between steps and do not replace with any explicit logic for any step again when you have context data.**
8.  **Do NOT use prompt inputs directly in your code.**
9. Interact with the environment (API, CLI, etc.) using the runtime data, not examples.
10. **Always validate and sanitize all input/output and do not create redundant steps that are already there in previous steps.**
11. The parameters provided (e.g., {{ parameters }}) will be sanitized by the framework/generated code to remove surrounding quotes. Do NOT apply .strip('"') or similar sanitization to them within the function body, as this will be handled automatically.
{% if previous_step_error %}
Previous attempt for this step failed with this error. Please fix:
{{ previous_step_error }}
{% endif %}
---
Please provide the correct code for the function body now:
"""),

    # --- FIX 2: Modified Godog LLM Prompt ---
    # Made the prompt much more explicit with separators for robust parsing.
    "godog": Template("""
You are a Go test automation expert using the Godog BDD framework.
Your task is to generate three distinct blocks of Go code for the Gherkin step provided below, separated by specific markers.

**Full Gherkin Feature:**
---
{{ full_feature_content }}
---

**Gherkin Step:** "{{ step_line }}"
**Scenario Context:**
{{ scenario_content }}

Framework: Godog (Go)
Environment: {{ environment }}
Parameters: {{ parameters }}
Values: {{ parameter_values }}
Extra context: {{ prompt_inputs }}

---
**INSTRUCTIONS:**
1.  You MUST structure your response into three parts, using the exact separators `---IMPORTS---`, `---CONTEXT-FIELDS---`, and `---LOGIC---`.
2.  **Do NOT include any other text, comments, or markdown code fences (e.g., ```go).**

---
**Part 1: Imports**
- After the `---IMPORTS---` separator, list all necessary Go `import` paths your code needs (e.g., `encoding/json`, `fmt`).
- Each import path should be on a new line.
- **Do NOT include `context` or `github.com/cucumber/godog` as they are already in the template.**

---
**Part 2: Context Fields**
- After the `---CONTEXT-FIELDS---` separator, declare any fields that need to be added to the `scenarioContext` struct for sharing state between steps.
- Each field declaration should be on a new line (e.g., `apiResponse []byte`, `sumResult int`).
- If no new fields are needed, leave this section empty.

---
**Part 3: Logic**
- After the `---LOGIC---` separator, write **only the Go code for the function body.**
- This function is a method on `*scenarioContext`. Use `s.` to access/store shared state (e.g., `s.apiResponse = body`).
- The `ctx` variable is for cancellation/timeouts only; do not use it for state.

---
**EXAMPLE RESPONSE FORMAT:**
---IMPORTS---
fmt
net/http
---CONTEXT-FIELDS---
apiResponse []byte
statusCode int
---LOGIC---
resp, err := http.Get(someURL)
if err != nil {
    return err
}
defer resp.Body.Close()
s.statusCode = resp.StatusCode
// ... more logic ...

---
Now, generate the response for the given step:
"""),

    "cucumber": Template("""
You are a test automation engineer writing Cucumber (Java) step definitions. Implement the code inside a `@Given`, `@When`, or `@Then` method for the provided step.

**Full Gherkin Feature:**
---
{{ full_feature_content }}
---

**Step:** "{{ step_line }}"
**Scenario:** ---
{{ scenario_content }}
---

Framework: Cucumber (Java)
Environment: {{ environment }}
Parameters: {{ parameters }}
Parameter values: {{ parameter_values }}
User context:
{{ prompt_inputs }}

---
**INSTRUCTIONS:**
1.  **Return only the method body.** No annotations, no method signature.
2.  **Do NOT include comments or markdown code fences (e.g., ```java).**
3.  Use exact parameter names: {{ parameters }}.
4.  Interact using Java code — for example, using RestAssured, Selenium, or Java ProcessBuilder.
5.  Use static/shared variables for state transfer if needed.
6.  Parse responses carefully, avoid hardcoding outputs.
7.  Add imports like `import io.restassured.RestAssured;` if used.
8.  Validate runtime output before comparing or asserting.
9.  Do not access the `prompt_inputs` directly in code — they are for structure understanding only.
{% if previous_step_error %}
Previous attempt for this step failed with this error. Please fix:
{{ previous_step_error }}
{% endif %}
---
Write the Cucumber Java method body now:
""")
}

def extract_context_vars_from_logic(logic: str) -> set:
    return set(re.findall(r"context\.([a-zA-Z_][a-zA-Z0-9_]*)", logic))

# --- FIX 3: Heavily Modified `generate_step_metadata` for Godog ---
def generate_step_metadata(step_text: str, framework: str, prompt_inputs: dict, environment: str, scenario_content: str, full_feature_content: str, previous_step_error: str = None) -> dict:

    step_keyword_match = re.match(r'^(Given|When|Then|And|But)\s+(.*)', step_text, flags=re.IGNORECASE)
    if not step_keyword_match:
        raise ValueError(f"Invalid Gherkin step: {step_text}")

    gherkin_keyword = step_keyword_match.group(1).lower()
    
    formatted_step_text, parameters = format_step_for_framework(step_text, framework)

    step_base = re.sub(r'[^a-z0-9]+', '_', re.sub(r'"[^"]+"', '', step_text).strip().lower())
    func_name = f"{step_base}_{hashlib.md5(step_text.encode()).hexdigest()[:8]}"

    actual_values = [match.group(1).strip('"') for match in re.finditer(r'"([^"]*)"', step_text)]
    named_value_map = {param: val for param, val in zip(parameters, actual_values)}

    parameter_values = {k: v.strip('"') for k, v in named_value_map.items()}
    prompt_template = FRAMEWORK_LOGIC_PROMPTS[framework]

    logic_prompt = prompt_template.render(
        full_feature_content=full_feature_content,
        step_line=step_text,
        step_text=formatted_step_text,
        scenario_content=scenario_content,
        parameters=parameters,
        parameter_values=parameter_values,
        environment=environment,
        prompt_inputs=json.dumps(prompt_inputs, indent=2),
        previous_step_error=previous_step_error
    )

    try:
        response = together_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": logic_prompt}],
            temperature=0.4
        )
        raw_llm_output = response.choices[0].message.content.strip()
        time.sleep(1)

        # Initialize defaults
        imports = []
        godog_fields = []
        final_logic = "pass" if framework == "behave" else "// TODO: Implement step logic"

        if framework == "godog":
            # This logic now robustly parses the structured output from the LLM
            parts = re.split(r'---IMPORTS---|---CONTEXT-FIELDS---|---LOGIC---', raw_llm_output)
            if len(parts) >= 4:
                imports_str, fields_str, logic_str = parts[1], parts[2], parts[3]
                imports = [line.strip() for line in imports_str.strip().splitlines() if line.strip()]
                godog_fields = [line.strip() for line in fields_str.strip().splitlines() if line.strip()]
                final_logic = textwrap.dedent(logic_str).strip()
            else:
                 print(f"[Warning] LLM output for Godog step '{step_text}' was not in the expected format. Using fallback.")
                 final_logic = "// TODO: Could not parse LLM output."

        else: # Existing logic for Behave and Cucumber
            raw_llm_output = re.sub(r'^\s*```(?:[a-zA-Z0-9_]+)?\s*$', '', raw_llm_output, flags=re.MULTILINE)
            raw_llm_output = re.sub(r'^(?:Here\'s the code:?|```\w*\n|```)\s*', '', raw_llm_output, flags=re.IGNORECASE | re.MULTILINE)
            raw_llm_output = re.sub(r'\s*(?:Hope this helps!?|Let me know if you have questions\.?|```\s*)$', '', raw_llm_output, flags=re.IGNORECASE | re.MULTILINE)
            raw_llm_output = raw_llm_output.strip()
            
            import_lines = re.findall(r'^\s*(?:import\s+[a-zA-Z0-9_.]+(?:\s+as\s+[a-zA-Z0-9_]+)?|from\s+[a-zA-Z0-9_.]+\s+import\s+(?:[a-zA-Z0-9_]+|\*|{[^}]+})(?:\s*,\s*(?:[a-zA-Z0-9_]+|\*|{[^}]+}))*)\s*$',
                                      raw_llm_output, re.MULTILINE)
            
            imports = sorted(set([line.strip() for line in import_lines]))
            logic_cleaned = raw_llm_output
            for imp_line in import_lines:
                logic_cleaned = logic_cleaned.replace(imp_line, '').strip()

            if framework == "behave":
                logic_cleaned = re.sub(r'^\s*def\s+\w+\(.*\):\s*$', '', logic_cleaned, flags=re.MULTILINE)
            elif framework == "cucumber":
                logic_cleaned = re.sub(r'^\s*(?:public\s+)?void\s+\w+\(.*\)\s*\{?\s*$', '', logic_cleaned, flags=re.MULTILINE)
                logic_cleaned = re.sub(r'^\s*\}?\s*$', '', logic_cleaned, flags=re.MULTILINE)

            final_logic = textwrap.dedent(logic_cleaned).strip()
            if not final_logic:
                final_logic = "pass" if framework == "behave" else "// TODO: Implement step logic"

            if framework == "behave" and parameters:
                quote_strips = [f"{param} = {param}.strip('\"')" for param in parameters]
                if final_logic.strip() and final_logic.strip() != "pass":
                    final_logic = "\n".join(quote_strips + [final_logic])
                else:
                    final_logic = "\n".join(quote_strips + ["pass"])
        
        step_result = {
            "func_name": func_name,
            "parameters": parameters,
            "step_text": formatted_step_text[len(gherkin_keyword) + 1:] if formatted_step_text.lower().startswith(gherkin_keyword + ' ') else formatted_step_text,
            "logic": final_logic,
            "imports": imports,
            "gherkin_keyword": gherkin_keyword
        }

        # Add the parsed Godog fields to the result dictionary
        if framework == "godog":
            step_result["scenario_context_fields"] = godog_fields

        return step_result


    except Exception as e:
        print(f"[LLM Error] Failed to generate logic for step: {step_text}\nDetails: {e}")
        return None

def generate_framework_code(all_step_metadata, framework, custom_imports, scenario_context_fields):
    if framework == "behave":
        return behave_template.render(
            step_imports=custom_imports,
            steps=[
                {
                    "step_text": step["step_text"],
                    "func_name": step["func_name"],
                    "parameters": step["parameters"],
                    "logic": step["logic"],
                    "gherkin_keyword": step["gherkin_keyword"] 
                }
                for step in all_step_metadata
            ]
        )

    elif framework == "godog":
        return godog_template.render(
            # Pass the aggregated custom imports and context fields to the template
            custom_imports=custom_imports,
            scenario_context_fields=scenario_context_fields,
            steps=[
                {
                    "func_name": step["func_name"],
                    "step_text": step["step_text"],
                    "parameters": step["parameters"],
                    "logic": step["logic"],
                    "gherkin_keyword": step["gherkin_keyword"]

                }
                for step in all_step_metadata
            ],
        )

    elif framework == "cucumber":
        return cucumber_step_template.render(
            custom_imports="\n".join(custom_imports),
            steps=[
                {
                    "func_name": step["func_name"],
                    "step_text": step["step_text"],
                    "parameters": step["parameters"],
                    "logic": step["logic"],
                    "gherkin_keyword": step["gherkin_keyword"]
                }
                for step in all_step_metadata
            ]
        )

    else:
        raise ValueError(f"Unsupported framework: {framework}")

# Write code to appropriate folders
def write_code(framework, feature_content, code, feature_filename):
    feature_path = Path("features") / feature_filename
    Path("features").mkdir(parents=True, exist_ok=True)
    feature_path.write_text(feature_content)

    if framework == "behave":
        Path("features/steps").mkdir(parents=True, exist_ok=True)
        path = Path("features/steps/step_definitions.py")
        path.write_text(code)
    elif framework == "godog":
        Path("godog").mkdir(parents=True, exist_ok=True)
        path = Path("godog/main_test.go")
        path.write_text(code)
    elif framework == "cucumber":
        base = Path("cucumber")
        stepdefs_dir = base / "src/test/java/stepdefinitions"
        runner_dir = base / "src/test/java/runner"
        features_dir = base / "src/test/resources/features"

        stepdefs_dir.mkdir(parents=True, exist_ok=True)
        runner_dir.mkdir(parents=True, exist_ok=True)
        features_dir.mkdir(parents=True, exist_ok=True)

        (stepdefs_dir / "StepDefinitions.java").write_text(code)
        (runner_dir / "TestRunner.java").write_text(cucumber_runner_template.render())
        (base / "pom.xml").write_text(pom_template.render())
        (features_dir / feature_filename).write_text(feature_content)

    return feature_path

def final_llm_autocorrect_code(feature_content: str, step_code: str, framework: str, previous_error: str = None) -> str:
    """
    Calls the LLM to review and autocorrect the generated step code.
    Optionally includes a previous error message for the LLM to fix.
    """
    prompt = f"""
You are an expert in test automation and {framework.upper()} BDD frameworks.

You are given a feature file and the full step definition file for the framework: {framework.upper()}.

Your job is to:
- Review the step definition file and ensure every step aligns correctly with the feature file.
- Automatically fix any runtime issues (e.g., using `json.loads` on a dict).
- Ensure consistent use of parameters, context/state (e.g., `context` or `ctx`), imports, function bodies, etc.
- **Do not include explanation or markdown code fences (e.g., ```python, ```go, ```javascript).** Just return the corrected file and do not change anything unless it is wrong.
- If you cannot fix it, return the original code without changes.
---

Feature File:
{feature_content}

---

Step Definitions File:
{step_code}
"""
    if previous_error:
        prompt += f"\n\n⚠️ Previous validation failed with this error. Please fix:\n{previous_error}"

    try:
        response = together_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        time.sleep(1) 
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] Final LLM correction failed: {e}")
        return step_code  # Return original if LLM fails

def validate_code(code, framework):
    try:
        if framework == 'behave':
            ast.parse(code)
            return True, None

        elif framework == 'godog':
            # Create a temporary directory to act as a Go module
            with tempfile.TemporaryDirectory() as temp_dir:
                temp_dir_path = Path(temp_dir)
                temp_file_path = temp_dir_path / "main_test.go"
                temp_file_path.write_text(code)

                # Initialize a Go module to handle dependencies
                subprocess.run(["go", "mod", "init", "tmp/mod"], cwd=temp_dir_path, capture_output=True, text=True)
                subprocess.run(["go", "mod", "tidy"], cwd=temp_dir_path, capture_output=True, text=True)

                # --- FIX ---
                # Use `go test -c` to compile test files without running them.
                # This is the correct way to validate a _test.go file.
                validate_result = subprocess.run(
                    ["go", "test", "-c"], 
                    cwd=temp_dir_path, 
                    capture_output=True, 
                    text=True
                )
                
                if validate_result.returncode != 0:
                    error_message = f"Go test compilation error detected:\n{validate_result.stderr.strip()}"
                    return False, error_message
            return True, None

        elif framework == 'cucumber':
            with tempfile.TemporaryDirectory() as temp_dir:
                base_path = Path(temp_dir)
                stepdefs_dir = base_path / "src/test/java/stepdefinitions"
                runner_dir = base_path / "src/test/java/runner"
                features_dir = base_path / "src/test/resources/features"

                stepdefs_dir.mkdir(parents=True, exist_ok=True)
                runner_dir.mkdir(parents=True, exist_ok=True)
                features_dir.mkdir(parents=True, exist_ok=True)

                (stepdefs_dir / "StepDefinitions.java").write_text(code)
                (runner_dir / "TestRunner.java").write_text(cucumber_runner_template.render())
                (base_path / "pom.xml").write_text(pom_template.render())
                (features_dir / "temp.feature").write_text("Feature: Temp\n  Scenario: Temp\n    Given a temp step")

                compile_result = subprocess.run(["mvn", "compile"], cwd=base_path, capture_output=True, text=True, shell=True)
                
                if compile_result.returncode != 0:
                    return False, f"Java compilation error detected by Maven: {compile_result.stderr.strip() or compile_result.stdout.strip()}"
            
            return True, None

        else:
            return False, f"Unsupported framework: {framework}"

    except Exception as e:
        return False, f"An unexpected error occurred during validation: {str(e)}"
# --- Main Execution Controller ---
def main():
    feature_file_path = input("Enter path to the .feature file (highlight parameters in steps): ").strip()
    feature_file_path =  Path("features") / feature_file_path
    if not feature_file_path.exists():
        print(f"File {feature_file_path} not found.")
        return

    feature_content = Path(feature_file_path).read_text()

    framework = input("Choose framework (behave / godog / cucumber): ").strip().lower()
    if framework not in {"behave", "godog", "cucumber"}:
        print("Unsupported framework")
        return

    inferred_env = infer_environment_with_llm(feature_content)
    environment = confirm_or_select_environment(inferred_env)
    prompt_inputs = load_and_collect_prompt_inputs(environment)

    available_context_vars = {} 
    scenarios_data = parse_feature_by_scenario(feature_content)
    
    all_step_metadata = []
    all_custom_imports = set()
    all_godog_fields = set() 
    
    for scenario in scenarios_data:
        print(f"\nProcessing Scenario: {scenario['title']}")
        for step_text in scenario["steps"]:
            prompt_inputs_with_context = dict(prompt_inputs)
            prompt_inputs_with_context["available_context"] = available_context_vars

            step_data = generate_step_metadata(
                step_text=step_text,
                framework=framework,
                prompt_inputs=prompt_inputs_with_context,
                environment=environment,
                scenario_content=scenario["content"],
                full_feature_content=feature_content,
                previous_step_error=None
            )

            if step_data is None:
                print(f"[ERROR] Skipping step due to LLM failure: {step_text}")
                continue

            all_step_metadata.append(step_data)
            all_custom_imports.update(step_data.get("imports", []))

            # --- FIX 4: Correctly update Godog context fields ---
            if framework == "godog":
                # This now correctly gets the fields parsed in generate_step_metadata
                all_godog_fields.update(step_data.get("scenario_context_fields", []))

            # Update context for Behave (or other frameworks if needed)
            if framework == "behave":
                used_vars = extract_context_vars_from_logic(step_data["logic"])
                for var in used_vars:
                    if var not in available_context_vars:
                        available_context_vars[var] = "Dynamically created from previous step"

    final_generated_code = None
    is_valid_code = False
    validation_error_message = None 

    for attempt in range(3):
        print(f"\nAttempt {attempt+1} to generate and validate final code...")

        code_to_autocorrect = generate_framework_code(
            all_step_metadata=all_step_metadata,
            framework=framework,
            custom_imports=sorted(list(all_custom_imports)),
            scenario_context_fields=sorted(list(all_godog_fields))
        )

        final_generated_code = final_llm_autocorrect_code(
            feature_content=feature_content,
            step_code=code_to_autocorrect,
            framework=framework,
            previous_error=validation_error_message
        )

        is_valid_code, validation_error_message = validate_code(final_generated_code, framework)

        if is_valid_code:
            print(f"Code validated successfully on attempt {attempt+1}.")
            break
        else:
            print(f"Attempt {attempt+1}: Validation failed with error:\n{validation_error_message}")

    if not is_valid_code:
        print("Final code validation failed after multiple attempts. Exiting.")
        return
    
    write_code(framework, feature_content, final_generated_code, Path(feature_file_path).name)

    print(f"Running {framework} tests...")
    if framework == "behave":
        subprocess.run(["behave", "features", "-f", "json.pretty", "-o", "report_behave.json"])
    elif framework == "godog":
        # Run tests from the `godog` directory where the file was written
        subprocess.run(["go", "test", "./..."], cwd="godog", shell=True)
    elif framework == "cucumber":
        subprocess.run(["mvn", "test"], cwd="cucumber", shell=True)

    print(f"[✓] {framework.capitalize()} test executed and report generated.")

if __name__ == '__main__':
    main()