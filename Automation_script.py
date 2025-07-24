import os
import re
import json
import ast
import time
import tempfile
import subprocess
from pathlib import Path
from pathlib import Path
from jinja2 import Template
import together
import yaml
import hashlib
import textwrap
from collections import namedtuple
from jinja2 import Environment

# Initialize Together AI client
together_client = together.Together(api_key=os.getenv("TOGETHER_API_KEY"))
LLM_MODEL="meta-llama/Llama-3.3-70B-Instruct-Turbo-Free"

def escape_java_regex(value: str) -> str:
    value = value.replace('\\', '\\\\')       # escape backslashes
    value = value.replace('"', '\\"')         # escape double quotes
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

godog_template = Template('''package main

// LLM-generated imports will go here
{% for line in step_imports %}
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
import io.cucumber.java.en.Given;
import io.cucumber.java.en.When;
import io.cucumber.java.en.Then;
import org.junit.Assert;

// Custom imports for step definitions
{% for line in custom_imports %}
import {{ line }};
{% endfor %}

public class StepDefinitions {

    {% for step in steps %}
    @{{ step.gherkin_keyword.lower() | capitalize }}("{{ step.step_text }}")
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

        user_input = input(f"Enter value for `{key}` (or type `skip` to skip): ").strip()

        if user_input.lower() == "skip":
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

    # Extract the Gherkin keyword and the rest of the step text
    step_keyword_match = re.match(r'^(Given|When|Then|And|But)\s+(.*)', step_text, flags=re.IGNORECASE)
    if step_keyword_match:
        # For Cucumber, the annotation pattern should NOT include the keyword itself.
        # It's like: @Given("I have a step") NOT @Given("Given I have a step")
        # For other frameworks, we might still want the full step text for matching.
        text_for_pattern = step_keyword_match.group(2).strip() # Capture only the part AFTER the keyword
    else:
        text_for_pattern = step_text # Fallback, though ideally input is always Gherkin step

    # --- IMPORTANT: Work with text_for_pattern from now on ---
    matches = list(re.finditer(r'"([^"]*)"', text_for_pattern))

    for i, match in enumerate(matches):
        static_part = text_for_pattern[last_end:match.start()]

        # Static parts for Godog need escaping. For Cucumber, they are literal.
        if framework == "godog":
            parts.append(re.escape(static_part).replace(r'\ ', ' '))
        else: # For behave and cucumber, just append the literal static part
            parts.append(static_part)

        preceding_text_in_pattern = text_for_pattern[:match.start()]
        preceding_words = re.findall(r'\b\w+\b', preceding_text_in_pattern) # Analyze words before the parameter
        if preceding_words:
            name = preceding_words[-1].lower()
            original_name = name
            k = 0
            while name in param_names: # Ensure unique parameter names
                k += 1
                name = f"{original_name}{k}"
        else:
            name = f"param{i}" # Fallback if no descriptive word

        param_names.append(name)

        if framework == "behave":
            parts.append(f"{{{name}}}") # Behave's format
        elif framework == "cucumber":
            parts.append("{string}") # Cucumber's preferred string parameter type
        elif framework == "godog":
            parts.append("([^\\\"]*)") # Godog's regex for capturing a string

        last_end = match.end()

    remaining_part = text_for_pattern[last_end:]
    if framework == "godog":
        parts.append(re.escape(remaining_part).replace(r'\ ', ' '))
    else: # For behave and cucumber, just append the literal remaining part
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

    "godog": Template("""
You are a Go test automation expert using Godog BDD. Write only the **function body** for the step below.

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
1. Return **only the raw Go code** – **no markdown code fences (e.g., ```go), no comments, no function signature, and no step decorator.**
2. This function is a method on `*scenarioContext`: `func (s *scenarioContext) StepName(ctx context.Context, ...) error`.
3. Use `s.` to **store/retrieve** shared state. Example: `s.podJson = value` or `val := s.podJson`.
4. Do **not** use `ctx.Context(...)` for state. `ctx` is for cancellation/timeouts only.
5. **List all necessary Go `import` statements** at the very top of your response, each on a new line, like `import "os/exec"` or `import "encoding/json"`.
6. **Immediately after the imports, declare any necessary shared variables as fields within the `scenarioContext` struct that your code uses. Provide the field name and its Go type, one per line.** For example:
    `podStatusJson map[string]interface{}`
    `clusterContext string`
    `minikubeStatus string`
7. Validate outputs/JSON before using.
8. Do **not** hardcode static data or sanitize parameters—they're already clean.
{% if previous_step_error %}
Previous attempt for this step failed with this error. Please fix:
{{ previous_step_error }}
{% endif %}
Output only the raw Go code body.
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
2.  **You MUST provide concrete, executable Java code for the step logic.** Do NOT return empty lines, placeholders, or comments without code. If you cannot provide a working solution, use `throw new io.cucumber.java.PendingException();`
3.  **DO NOT include comments or markdown code fences (e.g., ```java).** Your response should be raw Java code.
4.  **Include all necessary Java `import` statements at the very top of your generated method body.** Each import should be on a new line and end with a semicolon (e.g., `import java.io.IOException;`). These will be extracted and moved to the class level.
5.  Use exact parameter names: {{ parameters }}.
6.  Interact using Java code — for example, using RestAssured, Selenium, or Java ProcessBuilder.
7.  Use static/shared variables for state transfer if needed.
8.  Parse responses carefully, avoid hardcoding outputs.
9.  Validate runtime output before comparing or asserting.
10. Do not access the `prompt_inputs` directly in code — they are for structure understanding only.
11. **Do NOT include any surrounding class declarations, package declarations, or extra methods or unused imports or unused variables**
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

def filter_unused_imports(import_lines, logic, framework):
    """
    Returns only those imports that are actually used in the logic.
    """
    used_imports = []
    logic_text = logic if isinstance(logic, str) else "\n".join(logic)
    for imp in import_lines:
        if framework == "behave":
            # For Python: check if the imported module or symbol is used
            m = re.match(r'(?:from\s+([a-zA-Z0-9_.]+)\s+import\s+([a-zA-Z0-9_*,{} ]+))|(?:import\s+([a-zA-Z0-9_\.]+))', imp)
            if m:
                symbols = []
                if m.group(2):
                    # from ... import ...
                    symbols = [s.strip() for s in re.split(r',|{|}', m.group(2)) if s.strip() and s.strip() != '*']
                elif m.group(3):
                    # import ...
                    symbols = [m.group(3).split('.')[-1]]
                # If any symbol is used in logic, keep the import
                if any(re.search(r'\b' + re.escape(sym) + r'\b', logic_text) for sym in symbols):
                    used_imports.append(imp)
            else:
                used_imports.append(imp)  # fallback: keep if can't parse
        elif framework == "godog":
            # For Go: check if the imported package is used (very basic)
            m = re.match(r'import\s+"([a-zA-Z0-9_/\.]+)"', imp)
            if m:
                pkg = m.group(1).split('/')[-1]
                if re.search(r'\b' + re.escape(pkg) + r'\b', logic_text):
                    used_imports.append(imp)
            else:
                used_imports.append(imp)
        elif framework == "cucumber":
            # For Java: check if the class is used
            m = re.match(r'import\s+([a-zA-Z0-9_.]+)\.([A-Z][a-zA-Z0-9_]+);', imp)
            if m:
                class_name = m.group(2)
                if re.search(r'\b' + re.escape(class_name) + r'\b', logic_text):
                    used_imports.append(imp)
            else:
                used_imports.append(imp)
        else:
            used_imports.append(imp)
    return used_imports

def generate_step_metadata(step_text: str, framework: str, prompt_inputs: dict, environment: str, scenario_content: str, full_feature_content: str, previous_step_error: str = None) -> dict:

    step_keyword_match = re.match(r'^(Given|When|Then|And|But)\s+(.*)', step_text, flags=re.IGNORECASE)
    if not step_keyword_match:
        raise ValueError(f"Invalid Gherkin step: {step_text}")

    gherkin_keyword = step_keyword_match.group(1).lower()
    
    # Generate framework-specific step text + parameters
    formatted_step_text, parameters = format_step_for_framework(step_text, framework)

    # Clean function name
    step_base = re.sub(r'[^a-z0-9]+', '_', re.sub(r'"[^"]+"', '', step_text).strip().lower())
    func_name = f"{step_base}_{hashlib.md5(step_text.encode()).hexdigest()[:8]}"

    # Extract actual quoted values too
    actual_values = [match.group(1).strip('"') for match in re.finditer(r'"([^"]*)"', step_text)]
    named_value_map = {param: val for param, val in zip(parameters, actual_values)}

    parameter_values = {k: v.strip('"') for k, v in named_value_map.items()}
    # Prompt to LLM
    prompt_template = FRAMEWORK_LOGIC_PROMPTS[framework]

    logic_prompt = prompt_template.render(
        full_feature_content=full_feature_content, # New: Pass full feature content
        step_line=step_text, # New: Pass the original step line
        step_text=formatted_step_text, # This is the regex pattern for the step
        scenario_content=scenario_content,
        parameters=parameters,
        parameter_values=parameter_values,
        environment=environment,
        prompt_inputs=json.dumps(prompt_inputs, indent=2),
        previous_step_error=previous_step_error # New: Pass previous error for this step
    )

    try:
        response = together_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": logic_prompt}],
            temperature=0.4 # Keep temperature low for deterministic code
        )
        raw_llm_output = response.choices[0].message.content.strip()
        time.sleep(1)  # Add a delay between requests

        # --- Aggressive Cleaning of LLM Output ---
        # 1. Remove Markdown code fences (both start and end, and language specifier)
        raw_llm_output = re.sub(r'^\s*```(?:[a-zA-Z0-9_]+)?\s*$', '', raw_llm_output, flags=re.MULTILINE)

        # 2. Remove any common introductory/concluding remarks
        #    Example: "```python\nHere's the code:\n..." or "...```\nHope this helps!"
        raw_llm_output = re.sub(r'^(?:Here\'s the code:?|```\w*\n|```)\s*', '', raw_llm_output, flags=re.IGNORECASE | re.MULTILINE)
        raw_llm_output = re.sub(r'\s*(?:Hope this helps!?|Let me know if you have questions\.?|```\s*)$', '', raw_llm_output, flags=re.IGNORECASE | re.MULTILINE)
        raw_llm_output = raw_llm_output.strip()

        # 3. Extract Imports
        import_lines_set = set()
        logic_cleaned = raw_llm_output

        if framework == "cucumber":
            # Regex for Java imports: e.g., "import com.example.MyClass;"
            java_import_regex = r'^\s*import\s+[a-zA-Z0-9_.]+\s*;\s*$'
            found_java_imports = re.findall(java_import_regex, logic_cleaned, re.MULTILINE)
            for imp_line in found_java_imports:
                import_lines_set.add(imp_line.strip())
                logic_cleaned = logic_cleaned.replace(imp_line, '') # Remove from logic
        else: # For behave and godog (Python/Go imports)
            python_go_import_regex = r'^\s*(?:import\s+[a-zA-Z0-9_.]+(?:\s+as\s+[a-zA-Z0-9_]+)?|from\s+[a-zA-Z0-9_.]+\s+import\s+(?:[a-zA-Z0-9_]+|\*|{[^}]+})(?:\s*,\s*(?:[a-zA-Z0-9_]+|\*|{[^}]+}))*)\s*$'
            found_imports = re.findall(python_go_import_regex, logic_cleaned, re.MULTILINE)
            for imp_line in found_imports:
                import_lines_set.add(imp_line.strip())
                logic_cleaned = logic_cleaned.replace(imp_line, '') # Remove from logic
        # After extracting logic_cleaned and import_lines_set
        filtered_imports = filter_unused_imports(import_lines_set, logic_cleaned, framework)

        # 4. Remove accidental outer function definitions (LLM might sometimes try to define the function again)
        #    This regex is for Python 'def', adjust for 'func' (Go) or 'public void' (Java) as needed by framework
        if framework == "behave":
            logic_cleaned = re.sub(r'^\s*def\s+\w+\(.*\):\s*$', '', logic_cleaned, flags=re.MULTILINE)
        elif framework == "godog":
            # Godog functions are usually methods on a struct, or standalone funcs.
            # LLM might generate 'func MyFunc(...) {' or 's.MyMethod(...) {'
            logic_cleaned = re.sub(r'^\s*func\s+(?:\([sS]\s+\*\w+\))?\s*\w+\(.*\)\s*(?:[a-zA-Z.]+\s*)?\{?\s*$', '', logic_cleaned, flags=re.MULTILINE)
        elif framework == "cucumber": # Assuming Java
            logic_cleaned = re.sub(r'^\s*(?:public\s+)?void\s+\w+\(.*\)\s*\{?\s*$', '', logic_cleaned, flags=re.MULTILINE)
            logic_cleaned = re.sub(r'^\s*\}?\s*$', '', logic_cleaned, flags=re.MULTILINE) # Remove trailing '}'

        # 5. Dedent and strip final cleaned logic
        final_logic = textwrap.dedent(logic_cleaned).strip()

        # Handle empty logic if cleaning removed everything
        if not final_logic:
            # Use Cucumber's PendingException for Java, not generic "// TODO"
            if framework == "cucumber":
                final_logic = "throw new io.cucumber.java.PendingException();"
            else: # For other frameworks
                final_logic = "pass" if framework == "behave" else "// TODO: Implement step logic"


        # Always sanitize quoted string parameters for Behave
        if framework == "behave" and parameters:
            quote_strips = [f"{param} = {param}.strip('\"')" for param in parameters]
            if final_logic.strip() and final_logic.strip() != "pass":
                final_logic = "\n".join(quote_strips + [final_logic])
            else:
                final_logic = "\n".join(quote_strips + ["pass"])


        return {
            "func_name": func_name,
            "parameters": parameters,
            "step_text": formatted_step_text,
            "logic": final_logic,
            "imports": sorted(filtered_imports),
            "gherkin_keyword": gherkin_keyword
        }


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
            custom_imports=custom_imports,
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
            scenario_context_fields=scenario_context_fields
        )

    elif framework == "cucumber":
        return cucumber_step_template.render(
            custom_imports=custom_imports,
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
    # This is the crucial part that was missing or incorrect:
    if previous_error:
        prompt += f"\n\nPrevious validation failed with this error. Please fix:\n{previous_error}"

    try:
        response = together_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        time.sleep(1) # Added sleep to help with rate limits
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] Final LLM correction failed: {e}")
        return step_code  # Return original if LLM fails

def validate_code(code, framework):
    try:
        if framework == 'behave': # Python validation
            # Validate syntax using AST
            try:
                ast.parse(code)
                return True, None
            except SyntaxError as e:
                return False, f"Python syntax error: {str(e)}"

        elif framework == 'godog': # Go validation
            with tempfile.NamedTemporaryFile(delete=False, suffix=".go", mode='w') as temp_file:
                temp_file.write(code)
                temp_file_path = temp_file.name

            # Run `go fmt` to check for formatting issues and potential syntax errors
            # A non-zero exit code or stdout indicates issues
            fmt_result = subprocess.run(["gofmt", "-l", temp_file_path],
                                         capture_output=True, text=True)
            if fmt_result.returncode != 0 or fmt_result.stdout.strip():
                os.remove(temp_file_path)
                return False, f"Go formatting or syntax error detected by gofmt: {fmt_result.stderr.strip() or fmt_result.stdout.strip()}"

            # Run `go vet` for static analysis (common errors, suspicious constructs)
            vet_result = subprocess.run(["go", "vet", temp_file_path],
                                         capture_output=True, text=True)
            if vet_result.returncode != 0:
                os.remove(temp_file_path)
                return False, f"Go static analysis error detected by go vet: {vet_result.stderr.strip()}"

            # Run `go build` to check for compilation errors
            # This is the most definitive check for Go code validity
            build_result = subprocess.run(["go", "build", "-o", os.devnull, temp_file_path],
                                           capture_output=True, text=True)
            if build_result.returncode != 0:
                os.remove(temp_file_path)
                return False, f"Go compilation error detected by go build: {build_result.stderr.strip()}"

            os.remove(temp_file_path) # Clean up temporary file
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

                # Use 'mvn test-compile' or 'mvn test' to compile test sources
                # 'mvn test' is more comprehensive as it also runs tests, catching runtime issues.
                compile_result = subprocess.run(["mvn", "compile"], cwd=base_path, capture_output=True, text=True, shell=True)
                
                if compile_result.returncode != 0:
                    error_output = compile_result.stderr.strip() or compile_result.stdout.strip()
                    return False, f"Java compilation error detected by Maven:\n{error_output}"
            
            return True, None

        else:
            # This 'else' block should now only be hit if an truly unsupported framework is chosen
            return False, f"Unsupported framework: {framework}"

    except Exception as e:
        return False, f"An unexpected error occurred during validation: {str(e)}"


# --- Main Execution Controller ---
def main():

    # Step 1: Ask user for .feature file
    feature_file_path = input("Enter path to the .feature file (highlight parameters in steps): ").strip()
    feature_file_path = Path("features") / feature_file_path
    if not feature_file_path.exists():
        print(f"File {feature_file_path} not found.")
        return

    feature_content = Path(feature_file_path).read_text()

    # Step 2: Ask framework
    framework = input("Choose framework (behave / godog / cucumber): ").strip().lower()
    if framework not in {"behave", "godog", "cucumber"}:
        print("Unsupported framework")
        return

    # Step 3: Infer environment and get prompt inputs
    inferred_env = infer_environment_with_llm(feature_content)
    environment = confirm_or_select_environment(inferred_env)
    prompt_inputs = load_and_collect_prompt_inputs(environment)

    # Step 4: Extract steps
    available_context_vars = {}  # Tracks context variables and optional descriptions

    scenarios_data = parse_feature_by_scenario(feature_content)
    
    all_step_metadata = []
    all_custom_imports = set()
    all_godog_fields = set()  # For Godog, track scenario context fields
    
    for scenario in scenarios_data:
        print(f"\nProcessing Scenario: {scenario['title']}")
        for step_text in scenario["steps"]:

            # Pass available_context_vars to LLM
            prompt_inputs_with_context = dict(prompt_inputs)
            prompt_inputs_with_context["available_context"] = available_context_vars

            step_data = generate_step_metadata(
                step_text=step_text,
                framework=framework,
                prompt_inputs=prompt_inputs_with_context,
                environment=environment,
                scenario_content=scenario["content"],
                full_feature_content=feature_content, # Pass full feature content
                previous_step_error=None # No per-step retry currently, so always None
            )

            if step_data is None:
                print(f"[ERROR] Skipping step due to LLM failure: {step_text}")
                continue

            # Update known context vars from logic
            if framework == "behave":
                used_vars = extract_context_vars_from_logic(step_data["logic"])
            elif framework == "godog":
                used_vars = set(re.findall(r"s\.([a-zA-Z_][a-zA-Z0-9_]*)", step_data["logic"]))
                if "scenario_context_fields" in step_data:
                    for field_decl in step_data["scenario_context_fields"]:
                        all_godog_fields.add(field_decl)
            else: # Cucumber/Java
                used_vars = set()

            for var in used_vars:
                if var not in available_context_vars:
                    available_context_vars[var] = "Dynamically created from previous step"

            all_step_metadata.append(step_data)
            all_custom_imports.update(step_data.get("imports", []))


    # Step 5 & 6 (Combined): Generate and Validate Code with Retry
    final_generated_code = None
    is_valid_code = False
    validation_error_message = None # Initialize to None

    for attempt in range(3):
        print(f"\nAttempt {attempt+1} to generate and validate final code...")

        # Generate the framework code from collected step metadata
        # This is the initial rendering of the template
        code_to_autocorrect = generate_framework_code(
            all_step_metadata=all_step_metadata,
            framework=framework,
            custom_imports=sorted(list(all_custom_imports)),
            scenario_context_fields=sorted(list(all_godog_fields))
        )

        # Call the final LLM autocorrection on the generated code
        # Pass the previous error message if any
        final_generated_code = final_llm_autocorrect_code(
            feature_content=feature_content,
            step_code=code_to_autocorrect, # Pass the code generated from the template
            framework=framework,
            previous_error=validation_error_message # Pass the error from the previous attempt
        )

        # Validate the final code
        is_valid_code, validation_error_message = validate_code(final_generated_code, framework)

        if is_valid_code:
            print(f"Code validated successfully on attempt {attempt+1}.")
            break # Exit the retry loop
        else:
            print(f"[Retry] Attempt {attempt+1} failed. Retrying after LLM fix...")
            # The `validation_error_message` is already set for the next iteration.

    if not is_valid_code:
        print("\n Final code validation failed after 3 attempts.")
        print("Last encountered error:\n" + validation_error_message)
        return

    
    # Step 7: Write to file
    write_code(framework, feature_content, final_generated_code, Path(feature_file_path).name)

    # Step 8: Run tests
    print(f"Running {framework} tests...")
    if framework == "behave":
        subprocess.run(["behave", "features", "-f", "json.pretty", "-o", "report_behave.json"])
    elif framework == "godog":
        subprocess.run(["go", "test", "./godog", "-v"])
    elif framework == "cucumber":
        subprocess.run(["mvn", "test"], cwd="cucumber", shell=True)

    print(f"[✓] {framework.capitalize()} test executed and report generated.")

if __name__ == '__main__':
    main()