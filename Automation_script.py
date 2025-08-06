import os
import re
import json
import ast
from dotenv import load_dotenv
import time
import tempfile
import subprocess
from pathlib import Path
from jinja2 import Template, Environment
import together
import yaml
import hashlib
import textwrap
from collections import namedtuple

load_dotenv() # Load environment variables from .env file

# Initialize Together AI client
together_client = together.Together(api_key=os.getenv("TOGETHER_API_KEY"))
LLM_MODEL = "meta-llama/Llama-3.3-70B-Instruct-Turbo-Free" # Using your preferred model

def escape_java_regex(value: str) -> str:
    value = value.replace('\\', '\\\\')       # escape backslashes
    value = value.replace('"', '\\"')         # escape double quotes
    value = re.sub(r'\{[^}]+\}', '.*', value) # replace {params} with .*
    value = re.sub(r'\b(true|false|\d+)\b', '.*', value) # match literals
    return value

env = Environment()
env.filters['escape_java_regex'] = escape_java_regex
# Add tojson filter for passing dicts to LLM as JSON strings in templates
env.filters['tojson'] = json.dumps


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
// Add this for JSON comparison
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;


// Custom imports for step definitions
{% for line in custom_imports %}
import {{ line }};
{% endfor %}

public class StepDefinitions {

    // Shared state variables for Cucumber/Java
    // The LLM should be instructed to use these or declare its own if needed.
    public static String lastCommandOutput;
    public static String lastApiResponse;
    public static int lastResponseStatusCode;

    // Example: Load config file using user-provided filename
    public static JsonNode testConfig;
    static {
        try {
            ObjectMapper mapper = new ObjectMapper();
            InputStream in = StepDefinitions.class.getClassLoader().getResourceAsStream("{{ user_config_filename }}");
            testConfig = mapper.readTree(in);
        } catch (Exception e) {
            throw new RuntimeException("Failed to load config file: {{ user_config_filename }}", e);
        }
    }
    {% for step in steps %}
    @{{ step.gherkin_keyword.lower() | capitalize }}("{{ step.step_text | escape_java_regex }}")
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
    <dependency>
        <groupId>com.fasterxml.jackson.core</groupId>
        <artifactId>jackson-databind</artifactId>
        <version>2.16.1</version>
        <scope>test</scope>
    </dependency>
    <dependency>
        <groupId>org.apache.httpcomponents</groupId>
        <artifactId>httpclient</artifactId>
        <version>4.5.14</version>
        <scope>test</scope>
    </dependency>
  </dependencies>
</project>
''')

# --- LLM Prompts for generating Step Logic (UPDATED) ---
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
Parameters for this function: {{ parameters }}
Parameter values: {{ parameter_values }}
Available test configuration (JSON format):
{{ test_config | tojson }}

---
**IMPORTANT INSTRUCTIONS FOR YOUR RESPONSE:**
1.  **GENERATE ONLY THE CODE FOR THE FUNCTION BODY.** Do NOT include the function definition (e.g., `def func_name(...)`), decorators (e.g., `@given(...)`), or any surrounding boilerplate.
2.  **DO NOT wrap the code in markdown code blocks (e.g., ```python).** Provide raw code.
3.  **DO NOT include any comments, explanations, or extra text.** Just the executable code.
4.  **DO NOT define helper functions within this function.**
5.  Use the exact parameter names provided: {{ parameters }} in the steps instead of creating new variables.
6.  Ensure all necessary imports for your code are explicitly stated at the very top of your generated body, e.g., `import requests` or `from selenium import webdriver`.
7.  **Crucially, use the `context` object exclusively for storing and retrieving *actual runtime data* that flows between steps.** Example: `context.last_output = subprocess.check_output(...)` or `assert context.actual_status == expected_status`.
8.  **Access environment details and command/API templates from the `test_config` object.** For example, to run a command: `subprocess.run(context.test_config['commands']['get_pod_json_by_label'].format(label=label_param, namespace='default'), shell=True, capture_output=True, text=True)`.
9.  **For 'Then' steps, retrieve the actual output from `context` and compare it against the relevant expected output from `test_config.expected_outputs`.**
10. **Always validate and sanitize all input/output.**
11. The parameters provided (e.g., `{{ parameters }}`) will be sanitized by the framework/generated code to remove surrounding quotes. Do NOT apply `.strip('"')` or similar sanitization to them within the function body, as this will be handled automatically.
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
Parameters: {{ parameters }}
Values: {{ parameter_values }}
Available test configuration (JSON format):
{{ test_config | tojson }}

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
7. **Access environment details and command/API templates from the `test_config` object.** For example, to run a command: `cmd := exec.Command("kubectl", "get", "pod", "-l", label, "-o", "json")`.
8. **For 'Then' steps, retrieve the actual output from `s.` (e.g., `s.lastOutput`) and compare it against the relevant expected output from `test_config.expected_outputs`.**
9. Validate outputs/JSON before using.
10. Do **not** hardcode static data or sanitize parameters—they're already clean.
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
Parameters: {{ parameters }}
Parameter values: {{ parameter_values }}
Available test configuration (JSON format):
{{ test_config | tojson }}

---
**INSTRUCTIONS:**
1.  **Return only the method body.** No annotations, no method signature.
2.  **You MUST provide concrete, executable Java code for the step logic.** Do NOT return empty lines, placeholders, or comments without code. If you cannot provide a working solution, use `throw new io.cucumber.java.PendingException();`
3.  **DO NOT include comments or markdown code fences (e.g., ```java).** Your response should be raw Java code.
4.  **Include all necessary Java `import` statements at the very top of your generated method body.** Each import should be on a new line and end with a semicolon (e.g., `import java.io.IOException;`). These will be extracted and moved to the class level.
5.  Use exact parameter names: {{ parameters }}.
6.  **Access environment details and command/API templates from the `test_config` object.** For example: `String kubectlPath = testConfig.get("environment").get("kubectl_binary_path").asText();` or `String cmdTemplate = testConfig.get("commands").get("get_pod_json_by_label").asText();`
7.  **Store runtime results into static class variables like `StepDefinitions.lastCommandOutput`, `StepDefinitions.lastApiResponse`, `StepDefinitions.lastResponseStatusCode` so 'Then' steps can access them.**
8.  **For 'Then' steps, retrieve the actual output from `StepDefinitions` static variables and compare it against the relevant expected output from `test_config.expected_outputs`.** Use `Assert.assertEquals()` for simple values or `ObjectMapper` for JSON comparison.
9.  Parse responses carefully, avoid hardcoding outputs.
10. Validate runtime output before comparing or asserting.
11. Do not access the `test_config` directly in code — it's passed as a JSON string to the LLM for context. Instead, assume the LLM generates a Java Map/Object to parse this JSON config if it needs to use values from it. (Self-correction: The LLM can be instructed to directly parse the string or, better, we can inject a `testConfig` object if we manage to create it from the JSON. For simplicity, I've kept it as `tojson` for the LLM to process and generate parsing code. This is a common challenge with LLM code gen.)
12. **Do NOT include any surrounding class declarations, package declarations, or extra methods or unused imports or unused variables.**
{% if previous_step_error %}
Previous attempt for this step failed with this error. Please fix:
{{ previous_step_error }}
{% endif %}
---
Write the Cucumber Java method body now:
""")
}

def convert_text_to_bdd_file(input_path: Path, output_format: str):
    """
    Converts a text, .feature, or .spec file into a well-organized BDD file
    in the requested format ("gherkin" or "markdown") using the LLM.
    Writes the file to the appropriate folder and returns (output_file_path, content).
    """
    # Read the input file content
    input_content = input_path.read_text(encoding="utf-8")

    # Prepare the LLM prompt
    if output_format == "gherkin":
        prompt = f"""
You are an expert in writing Gherkin feature files for BDD frameworks.
Organize and rewrite the following content as a clean, well-formatted Gherkin feature file.
- Use correct Gherkin syntax (Feature, Scenario, Scenario Outline, Given, When, Then, Examples, etc.).
- Ensure all steps and examples are properly aligned and indented.
- Highlight any parameters in the steps using double quotes.
- Do not include any explanations or markdown code fences.
- Only output the .feature file content.

Content:
---
{input_content}
---
"""
        out_folder = Path("features")
        out_folder.mkdir(parents=True, exist_ok=True)
        out_ext = ".feature"
    elif output_format == "markdown":
        prompt = f"""
You are an expert in writing Gauge BDD specification files in Markdown format.
Organize and rewrite the following content as a clean, well-formatted Gauge spec file.
- Use correct Gauge Markdown syntax (e.g., # Specification, ## Scenario, steps, tables, etc.).
- Ensure all steps and examples are properly aligned and indented.
- Do not include any explanations or markdown code fences.
- Highlight any parameters in the steps using double quotes.
- Only output the .spec file content.

Content:
---
{input_content}
---
"""
        out_folder = Path("markdown")
        out_folder.mkdir(parents=True, exist_ok=True)
        out_ext = ".spec"
    else:
        raise ValueError("Unsupported output format: must be 'gherkin' or 'markdown'")

    # Call the LLM to organize the content
    try:
        response = together_client.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        time.sleep(1)  # Give some time to the LLM to process
        organized_content = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"[ERROR] LLM failed to convert file: {e}")
        return None, None

    # Determine output file name
    base_name = input_path.stem
    output_file_path = out_folder / f"{base_name}{out_ext}"

    # Write the organized content to the output file
    output_file_path.write_text(organized_content, encoding="utf-8")

    return str(output_file_path), organized_content


StepBody = namedtuple("StepBody", ["params", "code"])

# RENAMED and MODIFIED to load from file
def load_test_config(filename="config.yaml", start_path=None):
    """
    Search upwards from the current script or a provided path to find and load config.yaml.
    """
    start = Path(start_path or __file__).resolve()
    for path in [start] + list(start.parents):
        config_file = path / filename
        if config_file.is_file():
            with open(config_file, "r") as f:
                print(f"Loaded test configuration from: {config_file}")
                return yaml.safe_load(f)
    print(f"Config file '{filename}' not found from path {start}")
    return None

def extract_steps_from_feature(feature_content: str) -> list[str]:
    """
    Extracts Gherkin step lines (Given, When, Then, And, But) from feature content.
    Ignores comments, Scenario/Feature declarations, and whitespace.
    """
    step_pattern = re.compile(r'^\s*(Given|When|Then|And|But)\b', re.IGNORECASE)
    step_lines = []

    for line in feature_content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("feature:") or stripped.lower().startswith("scenario:") or stripped.lower().startswith("scenario outline:") or stripped.lower().startswith("examples:"):
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
            parts.append(f'"{{{name}}}"') # Behave's format with quotes
        elif framework == "cucumber":
            # For Cucumber, use {string} for quoted strings or a more specific type if known
            # Using {string} allows Cucumber to handle the parameter extraction
            parts.append("{string}") 
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
    current_scenario_title = None
    scenario_lines = []
    
    for line in feature_content.splitlines():
        stripped_line = line.strip()
        if stripped_line.lower().startswith("scenario:") or stripped_line.lower().startswith("scenario outline:"):
            if current_scenario_title:
                scenarios.append({
                    "title": current_scenario_title,
                    "content": "\n".join(scenario_lines),
                    "steps": extract_steps_from_feature("\n".join(scenario_lines))
                })
            current_scenario_title = stripped_line
            scenario_lines = [line] # Keep original line with indentation
        elif current_scenario_title:
            scenario_lines.append(line) # Keep original line with indentation
            
    if current_scenario_title: # Add the last scenario
        scenarios.append({
            "title": current_scenario_title,
            "content": "\n".join(scenario_lines),
            "steps": extract_steps_from_feature("\n".join(scenario_lines))
        })
    return scenarios

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
            else:
                used_imports.append(imp) # If regex fails, assume it's used
        elif framework == "godog":
            # For Go: check if the imported package is used (very basic)
            m = re.match(r'import\s+"([a-zA-Z0-9_/\.]+)"', imp)
            if m:
                pkg = m.group(1).split('/')[-1]
                if re.search(r'\b' + re.escape(pkg) + r'\b', logic_text):
                    used_imports.append(imp)
            else:
                used_imports.append(imp) # If regex fails, assume it's used
        elif framework == "cucumber":
            # For Java: check if the class is used
            m = re.match(r'import\s+([a-zA-Z0-9_.]+)\.([A-Z][a-zA-Z0-9_]+);', imp)
            if m:
                class_name = m.group(2)
                if re.search(r'\b' + re.escape(class_name) + r'\b', logic_text):
                    used_imports.append(imp)
            else:
                used_imports.append(imp) # If regex fails, assume it's used
        else:
            used_imports.append(imp)
    return used_imports

def generate_step_metadata(step_text: str, framework: str, test_config: dict, scenario_content: str, full_feature_content: str, previous_step_error: str = None) -> dict: # Changed prompt_inputs to test_config

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
        full_feature_content=full_feature_content,
        step_line=step_text,
        step_text=formatted_step_text,
        scenario_content=scenario_content,
        parameters=parameters,
        parameter_values=parameter_values,
        environment="GENERIC", # Removed specific env variable, now covered by test_config
        test_config=test_config, # Pass the entire loaded test_config here
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

        # --- Aggressive Cleaning of LLM Output ---
        # 1. Remove Markdown code fences (both start and end, and language specifier)
        raw_llm_output = re.sub(r'^\s*```(?:[a-zA-Z0-9_]+)?\s*$', '', raw_llm_output, flags=re.MULTILINE)

        # 2. Remove any common introductory/concluding remarks
        raw_llm_output = re.sub(r'^(?:Here\'s the code:?|```\w*\n|```)\s*', '', raw_llm_output, flags=re.IGNORECASE | re.MULTILINE)
        raw_llm_output = re.sub(r'\s*(?:Hope this helps!?|Let me know if you have questions\.?|```\s*)$', '', raw_llm_output, flags=re.IGNORECASE | re.MULTILINE)
        raw_llm_output = raw_llm_output.strip()

        # 3. Extract Imports
        import_lines_set = set()
        logic_cleaned = raw_llm_output

        if framework == "cucumber":
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
        
        filtered_imports = filter_unused_imports(import_lines_set, logic_cleaned, framework)

        # 4. Remove accidental outer function definitions (LLM might sometimes try to define the function again)
        if framework == "behave":
            logic_cleaned = re.sub(r'^\s*def\s+\w+\(.*\):\s*$', '', logic_cleaned, flags=re.MULTILINE)
        elif framework == "godog":
            logic_cleaned = re.sub(r'^\s*func\s+(?:\([sS]\s+\*\w+\))?\s*\w+\(.*\)\s*(?:[a-zA-Z.]+\s*)?\{?\s*$', '', logic_cleaned, flags=re.MULTILINE)
        elif framework == "cucumber": # Assuming Java
            logic_cleaned = re.sub(r'^\s*(?:public\s+)?void\s+\w+\(.*\)\s*\{?\s*$', '', logic_cleaned, flags=re.MULTILINE)
            logic_cleaned = re.sub(r'^\s*\}?\s*$', '', logic_cleaned, flags=re.MULTILINE) # Remove trailing '}'

        # 5. Dedent and strip final cleaned logic
        final_logic = textwrap.dedent(logic_cleaned).strip()

        # Handle empty logic if cleaning removed everything
        if not final_logic:
            if framework == "cucumber":
                final_logic = "throw new io.cucumber.java.PendingException();"
            else:
                final_logic = "pass" if framework == "behave" else "// TODO: Implement step logic"

        # Always sanitize quoted string parameters for Behave. Instruction for LLM handles this now.
        if framework == "behave" and parameters:
            # Removed the explicit strip logic here, as LLM is instructed not to do it
            pass

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

def generate_framework_code(all_step_metadata, framework, custom_imports, scenario_context_fields, user_config_filename=None):
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
            step_imports=custom_imports,
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
            ],
            user_config_filename=user_config_filename,  # Placeholder for user config filename
        )

    else:
        raise ValueError(f"Unsupported framework: {framework}")

# Write code to appropriate folders
def write_code(framework, feature_content, code, feature_filename, config_path=None, user_config_filename=None):
    # This writes the feature file to the root `features` directorys
    # The framework-specific writing handles copying it into its project structure
    feature_path = Path("features") / feature_filename
    Path("features").mkdir(parents=True, exist_ok=True)
    feature_path.write_text(feature_content)

    if framework == "behave":
        Path("behave/features/steps").mkdir(parents=True, exist_ok=True)
        path = Path("behave/features/steps/step_definitions.py")
        path.write_text(code)
        env_py = Path("behave/features/environment.py")
        env_py.write_text(
            "import yaml\n"
            "import os\n"
            "def before_all(context):\n"
            "    config_path = os.path.join(os.path.dirname(__file__), '..', 'config.yaml')\n"
            "    with open(config_path, 'r') as f:\n"
            "        context.test_config = yaml.safe_load(f)\n"
        )
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
        
        resources_config_path = Path("cucumber/src/test/resources") / user_config_filename
        resources_config_path.parent.mkdir(parents=True, exist_ok=True)
        resources_config_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")

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
- Automatically fix any runtime issues (e.g., using `json.loads` on a dict, incorrect variable usage, missing imports, syntax errors).
- Ensure consistent use of parameters, context/state (e.g., `context` or `StepDefinitions.lastCommandOutput`), imports, function bodies, etc.
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

            fmt_result = subprocess.run(["gofmt", "-l", temp_file_path],
                                         capture_output=True, text=True)
            if fmt_result.returncode != 0 or fmt_result.stdout.strip():
                os.remove(temp_file_path)
                return False, f"Go formatting or syntax error detected by gofmt: {fmt_result.stderr.strip() or fmt_result.stdout.strip()}"

            vet_result = subprocess.run(["go", "vet", temp_file_path],
                                         capture_output=True, text=True)
            if vet_result.returncode != 0:
                os.remove(temp_file_path)
                return False, f"Go static analysis error detected by go vet: {vet_result.stderr.strip()}"

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
                # A minimal feature file is needed for Maven compilation to succeed
                (features_dir / "temp.feature").write_text("Feature: Temp\n  Scenario: Temp\n    Given a temp step")

                compile_result = subprocess.run(["mvn", "compile"], cwd=base_path, capture_output=True, text=True, shell=False) # Changed shell=False for security and portability
                
                if compile_result.returncode != 0:
                    error_output = compile_result.stderr.strip() or compile_result.stdout.strip()
                    return False, f"Java compilation error detected by Maven:\n{error_output}"
            
            return True, None

        else:
            return False, f"Unsupported framework: {framework}"

    except Exception as e:
        return False, f"An unexpected error occurred during validation: {str(e)}"


# --- Main Execution Controller ---
def main():
    # Step 1: Ask user for input text file (now just path to .feature or .txt)
    input_text_path_str = input("Enter path to the input (.feature file or plain text file): ").strip()
    input_text_path = Path("input_file")/input_text_path_str
    if not input_text_path.exists():
        print(f"File {input_text_path} not found.")
        return

    config_path_str = input("Enter path to your environment details file: ").strip()
    config_path = Path(config_path_str)
    if not config_path.exists():
        print(f"Config file {config_path} not found.")
        return
    
    user_config_filename = config_path.name
    # Step 2: Ask framework (removed Gauge as a framework for code generation, kept as output format)
    framework = input("Choose framework (behave / godog / cucumber): ").strip().lower()
    if framework not in {"behave", "godog", "cucumber"}:
        print("Unsupported framework. Please choose 'behave', 'godog', or 'cucumber'.")
        return

    # Step 3: Determine BDD file format (fixed to gherkin if code generation)
    # If the user provides a plain text file, we convert it to gherkin.
    # If they provide a .feature file, we still pass it through LLM for organization,
    # but the format remains gherkin.
    bdd_output_format = "gherkin" # Fixed to gherkin for code-generating frameworks

    # Step 4: Handle input file type - always convert/reorganize to Gherkin
    print(f"Processing input file {input_text_path.name} to Gherkin format...")
    output_file_path, feature_content = convert_text_to_bdd_file(input_text_path, bdd_output_format)
    if not output_file_path or not Path(output_file_path).exists():
        print("Failed to generate/organize BDD feature file with LLM. Exiting.")
        return

    feature_file_path = Path(output_file_path)
    feature_filename = feature_file_path.name # Get the filename only

    print(f"Feature file ready at: {feature_file_path}")
    print(f"\n--- Generated/Organized Feature Content ---\n{feature_content}\n---------------------------------------\n")


    # Step 5: Load Test Configuration (from project root or nearest parent)
    if framework == "behave":
        framework_config_path = Path("behave") /  user_config_filename
    elif framework == "godog":
        framework_config_path = Path("godog") / user_config_filename
    elif framework == "cucumber":
        framework_config_path = Path("cucumber") / user_config_filename
    else:
        print("Unsupported framework.")
        return

    framework_config_path.parent.mkdir(parents=True, exist_ok=True)
    framework_config_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")

    test_config = load_test_config(filename=user_config_filename, start_path=framework_config_path.parent)

    # Step 6: Parse Feature by Scenario and Generate Step Metadata
    scenarios_data = parse_feature_by_scenario(feature_content)
    
    all_step_metadata = []
    all_custom_imports = set()
    all_godog_fields = set() 
    
    for scenario in scenarios_data:
        print(f"\nProcessing Scenario: {scenario['title']}")
        for step_text in scenario["steps"]:
            print(f"Generating logic for step: \"{step_text}\"")
            # Pass the loaded test_config directly
            step_data = generate_step_metadata(
                step_text=step_text,
                framework=framework,
                test_config=test_config, # Passing the loaded config
                scenario_content=scenario["content"],
                full_feature_content=feature_content,
                previous_step_error=None # No per-step retry currently
            )

            if step_data is None:
                print(f"[ERROR] Skipping step due to LLM failure: {step_text}")
                continue
            
            all_step_metadata.append(step_data)
            all_custom_imports.update(step_data.get("imports", []))
            # For Godog, extract scenario context fields from LLM's raw output if it returns them
            # (though the prompt instructs it to list them separately for godog_template rendering)
            if framework == "godog":
                # Assuming the LLM will provide these in the format requested by the prompt
                # You might need more sophisticated parsing here if LLM doesn't adhere strictly
                godog_fields_from_llm = re.findall(r'^\s*([a-zA-Z_][a-zA-Z0-9_]*\s+[a-zA-Z_][a-zA-Z0-9_]*)\s*$', step_data["logic"], re.MULTILINE)
                for field_decl in godog_fields_from_llm:
                    all_godog_fields.add(field_decl)


    # Step 7: Generate and Validate Code with Retry
    final_generated_code = None
    is_valid_code = False
    validation_error_message = None

    # Iteratively try to generate and validate the full code
    for attempt in range(3):
        print(f"\nAttempt {attempt+1} to generate and validate final code...")

        # Generate the framework code from collected step metadata
        code_to_autocorrect = generate_framework_code(
            all_step_metadata=all_step_metadata,
            framework=framework,
            custom_imports=sorted(list(all_custom_imports)),
            scenario_context_fields=sorted(list(all_godog_fields)),
            user_config_filename=user_config_filename  # Pass user config filename
        )

        # Call the final LLM autocorrection on the generated code
        final_generated_code = final_llm_autocorrect_code(
            feature_content=feature_content,
            step_code=code_to_autocorrect,
            framework=framework,
            previous_error=validation_error_message # Pass error from previous attempt
        )

        # Validate the final code
        is_valid_code, validation_error_message = validate_code(final_generated_code, framework)

        if is_valid_code:
            print(f"Code validated successfully on attempt {attempt+1}.")
            break # Exit retry loop
        else:
            print(f"[Retry] Attempt {attempt+1} failed. Error:\n{validation_error_message}\nRetrying after LLM fix...")
    
    if not is_valid_code:
        print("\n!!! Final code validation failed after all attempts. !!!")
        print("Last encountered error:\n" + validation_error_message)
        # Write the flawed code so user can inspect
        write_code(framework, feature_content, final_generated_code, feature_filename)
        print(f"Generated code (with errors) saved to respective framework directory for inspection.")
        return # Exit if code is not valid after retries
        
    # Step 8: Write to file
    print("\nWriting generated code to project structure...")
    write_code(framework, feature_content, final_generated_code, feature_filename, config_path, user_config_filename)
    print("Code written successfully.")

    # Step 9: Run tests and validate 
    print(f"\nRunning {framework} tests...")
    test_run_success = False
    if framework == "behave":
        # Using `json.pretty` formatter to get results
        behave_dir = Path("behave")
        behave_features_dir = behave_dir / "features"
        behave_steps_dir = behave_features_dir / "steps"
        behave_report_path = behave_dir / "report_behave.json"

        # Ensure behave/features and steps exist
        behave_features_dir.mkdir(parents=True, exist_ok=True)
        behave_steps_dir.mkdir(parents=True, exist_ok=True)

        # Copy the feature file to behave/features/
        src_feature_file = Path("features") / feature_filename
        dst_feature_file = behave_features_dir / feature_filename
        dst_feature_file.write_text(src_feature_file.read_text(encoding="utf-8"), encoding="utf-8")

        
        # Ensure behave project structure is ready for execution
        # subprocess.run ensures Behave can find features/steps
        result = subprocess.run(
            ["behave", "--format", "json.pretty", "--outfile", str(behave_report_path), f"features/{feature_filename}"],
            cwd=behave_dir,
            capture_output=True,
            text=True
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        
        if result.returncode == 0:
            print("Behave tests executed. Checking report for detailed results.")
            if behave_report_path.exists():
                try:
                    with open(behave_report_path, 'r') as f:
                        report_data = json.load(f)
                    
                    total_scenarios = 0
                    failed_scenarios = 0
                    for feature in report_data:
                        for scenario in feature.get('elements', []):
                            total_scenarios += 1
                            scenario_failed = any(
                                step.get('result', {}).get('status') == 'failed'
                                for step in scenario.get('steps', [])
                            )
                            if scenario_failed:
                                failed_scenarios += 1
                    
                    if failed_scenarios == 0 and total_scenarios > 0:
                        print(f"All {total_scenarios} Behave scenarios passed!")
                        test_run_success = True
                    else:
                        print(f"Behave tests completed: {passed_scenarios}/{total_scenarios} scenarios passed.")
                        test_run_success = False
                except json.JSONDecodeError:
                    print(f"Error reading Behave JSON report at {behave_report_path}")
                    test_run_success = False
            else:
                print("Behave JSON report not found.")
                test_run_success = False
        else:
            print(f"Behave test execution failed with exit code {result.returncode}.")
            test_run_success = False

    elif framework == "godog":
        godog_dir = Path("godog")
        result = subprocess.run(
            ["go", "test", "./..."], # Assuming main_test.go is directly in godog folder
            cwd=godog_dir,
            capture_output=True,
            text=True
        )
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
        
        if "--- FAIL:" in result.stdout or result.returncode != 0:
            print("Godog tests failed.")
            test_run_success = False
        else:
            print("Godog tests passed!")
            test_run_success = True

    elif framework == "cucumber":
        cucumber_project_path = Path("cucumber")
        # Use `mvn clean test` to compile and run tests
        result = subprocess.run(
            ["mvn", "clean", "test"],
            cwd=cucumber_project_path,
            capture_output=True,
            text=True,
            shell=False # Prefer not to use shell=True unless necessary for command parsing
        )

        print("\n--- Maven Build and Test Output ---")
        print(result.stdout)
        if result.stderr:
            print("\n--- Maven Error Output ---")
            print(result.stderr)

        if result.returncode == 0:
            print("\nMaven build and tests completed successfully.")
            # Parse Cucumber's JSON Report for overall pass/fail
            cucumber_report_path = cucumber_project_path / "target/cucumber-report.json"
            if cucumber_report_path.exists():
                try:
                    with open(cucumber_report_path, 'r') as f:
                        cucumber_report = json.load(f)

                    total_scenarios = 0
                    passed_scenarios = 0
                    
                    for feature in cucumber_report:
                        for scenario in feature.get('elements', []):
                            total_scenarios += 1
                            all_steps_passed = True
                            for step in scenario.get('steps', []):
                                if step['result']['status'] != 'passed':
                                    all_steps_passed = False
                                    break
                            if all_steps_passed:
                                passed_scenarios += 1
                    
                    print(f"\n--- Test Summary ---")
                    print(f"Total Scenarios: {total_scenarios}")
                    print(f"Passed Scenarios: {passed_scenarios}")

                    if total_scenarios > 0 and passed_scenarios == total_scenarios:
                        print("All Cucumber tests passed!")
                        test_run_success = True
                    else:
                        print("Some Cucumber tests failed!")
                        test_run_success = False
                except json.JSONDecodeError:
                    print(f"Error reading Cucumber JSON report at {cucumber_report_path}. Report might be malformed or empty.")
                    test_run_success = False
            else:
                print("Cucumber report not found. Cannot determine detailed test results.")
                test_run_success = False # Assume failure if report is missing
        else:
            print(f"\nMaven build or tests failed with exit code {result.returncode}.")
            test_run_success = False

    if test_run_success:
        print(f"\n[✓] {framework.capitalize()} test execution completed. All tests passed based on report.")
    else:
        print(f"\n[X] {framework.capitalize()} test execution completed. Some tests failed based on report.")

if __name__ == '__main__':
    main()