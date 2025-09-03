from langchain_openai import ChatOpenAI
from langchain.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain.agents import tool
from langchain import hub
from langchain.agents import create_react_agent, AgentExecutor

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
from openai import OpenAI
import yaml
import hashlib
import textwrap
from collections import namedtuple

load_dotenv()

LLM_MODEL = "llama-4-scout-17b-16e-instruct"

# --- LangChain LLM Initialization ---
# This replaces direct `OpenAI` client for LangChain operations
# It's more modular and integrates with the entire LangChain ecosystem.
llm = ChatOpenAI(
    base_url="https://api.cerebras.ai/v1",
    api_key=os.environ.get("CEREBRAS_API_KEY"),
    model_name=LLM_MODEL,
    temperature=0.2 # Control creativity within the LLM object
)

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

env_template = """
import yaml
import os
def before_all(context):
    config_path = os.path.join(os.path.dirname(__file__), '{user_config_filename}')
    with open(config_path, 'r') as f:
        context.test_config = yaml.safe_load(f)
"""

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
import org.yaml.snakeyaml.Yaml;

// Imports for robust config loading
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;

import java.io.InputStream;
import java.util.Map;

// LLM-generated custom imports will be placed here
{% for line in custom_imports %}
import {{ line }};
{% endfor %}

public class StepDefinitions {

    // The testConfig is a JsonNode, which is easy and safe to query.
    private static JsonNode testConfig;

    // Static variables to share state between steps
    public static String lastCommandOutput;
    public static io.restassured.response.Response lastApiResponse; // If using RestAssured
    public static int lastResponseStatusCode;

    // Static block to load the config file once
    static {
        try (InputStream in = StepDefinitions.class.getClassLoader().getResourceAsStream("{{ user_config_filename }}")) {
            if (in == null) {
                throw new RuntimeException("Config file not found: {{ user_config_filename }}");
            }
            Yaml yaml = new Yaml();
            Map<String, Object> yamlData = yaml.load(in);
            ObjectMapper objectMapper = new ObjectMapper();
            testConfig = objectMapper.valueToTree(yamlData);
        } catch (Exception e) {
            throw new RuntimeException("Failed to load or parse test config", e);
        }
    }

    // The template now iterates through each step and builds the full method for it.
    // The LLM only provides the "logic" part.
    {% for step in steps %}
    @{{ step.gherkin_keyword.lower() | capitalize }}("{{ step.step_text | escape_java_regex }}")
    public void {{ step.func_name }}({% if step.parameters %}{% for param in step.parameters %}String {{ param }}{% if not loop.last %}, {% endif %}{% endfor %}{% endif %}) throws Exception {
        // LLM-generated logic goes here
{{ step.logic | indent(8) }}
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
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 
                             http://maven.apache.org/xsd/maven-4.0.0.xsd">

    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>cucumber-tests</artifactId>
    <version>1.0-SNAPSHOT</version>
    <packaging>jar</packaging>

    <properties>
        <maven.compiler.source>11</maven.compiler.source>
        <maven.compiler.target>11</maven.compiler.target>
        <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
        <cucumber.version>7.18.1</cucumber.version>
        <junit.version>4.13.2</junit.version>
        <jackson.version>2.17.1</jackson.version>
    </properties>

    <dependencies>
    <!-- Cucumber Core + Java -->
    <dependency>
        <groupId>io.cucumber</groupId>
        <artifactId>cucumber-java</artifactId>
        <version>${cucumber.version}</version>
        <scope>test</scope>
    </dependency>

    <!-- Cucumber JUnit Runner -->
    <dependency>
        <groupId>io.cucumber</groupId>
        <artifactId>cucumber-junit</artifactId>
        <version>${cucumber.version}</version>
        <scope>test</scope>
    </dependency>

    <!-- SnakeYAML (for loading config.yaml) -->
    <dependency>
        <groupId>org.yaml</groupId>
        <artifactId>snakeyaml</artifactId>
        <version>2.2</version>
    </dependency>

    <!-- Rest Assured -->
    <dependency>
        <groupId>io.rest-assured</groupId>
        <artifactId>rest-assured</artifactId>
        <version>5.4.0</version>
        <scope>test</scope>
    </dependency>

    <!-- JUnit -->
    <dependency>
        <groupId>junit</groupId>
        <artifactId>junit</artifactId>
        <version>${junit.version}</version>
        <scope>test</scope>
    </dependency>

    <!-- Jackson (JSON parsing for config/step data) -->
    <dependency>
        <groupId>com.fasterxml.jackson.core</groupId>
        <artifactId>jackson-databind</artifactId>
        <version>${jackson.version}</version>
    </dependency>

    <!-- Logging -->
    <dependency>
        <groupId>org.slf4j</groupId>
        <artifactId>slf4j-simple</artifactId>
        <version>2.0.12</version>
        <scope>test</scope>
    </dependency>
</dependencies>

    <build>
        <plugins>
            <!-- Compiler -->
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-compiler-plugin</artifactId>
                <version>3.11.0</version>
                <configuration>
                    <source>${maven.compiler.source}</source>
                    <target>${maven.compiler.target}</target>
                </configuration>
            </plugin>

            <!-- Surefire for running JUnit tests -->
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-surefire-plugin</artifactId>
                <version>3.2.5</version>
                <configuration>
                    <includes>
                        <include>**/*Test.java</include>
                        <include>**/*Runner.java</include>
                    </includes>
                </configuration>
            </plugin>
        </plugins>
    </build>

</project>
''')


# --- LLM Prompts for generating Step Logic (UPDATED) ---
FRAMEWORK_LOGIC_PROMPTS = {
    "behave": """
You are a Python test automation expert using Behave. Your ONLY task is to write the Python code that goes inside a method body to implement a single Gherkin step.

**THE ROLE OF A 'Then' STEP IS TO EXTRACT DATA FOR LATER VERIFICATION.**
- For **Given/When** steps, generate code to interact with the system and store raw results in `context.lastCommandOutput`.
- For **Then** steps, your job is to:
    1. Create a descriptive, unique `lookupKey` in `camelCase` from the step text.
    2. Extract the actual value from `context.lastCommandOutput`.
    3. Write a single JSON file named `test_result.json` containing BOTH the `lookupKey` and the `actualValue`.

---
**CRITICAL RULES:**
1.  **YOUR RESPONSE MUST BE ONLY THE RAW PYTHON CODE FOR THE METHOD'S BODY.** Do not include the method signature, decorators, comments, or markdown.
2.  **'Then' STEPS MUST NOT USE `assert`.** They only extract data.
3.  **HOW TO WRITE THE JSON RESULT FILE (for a 'Then' step):**
    *   You MUST use the following pattern, writing the file to the root of the project.

    ```python
    # --- START 'THEN' STEP EXAMPLE PATTERN ---
    # 1. Create a dynamic lookup key from the Gherkin step.
    # For a step like "the user's name should be 'Alice'", a good key would be "userName".
    # For "the pod status should be 'Running'", a good key is "podStatus".
    lookup_key = "podStatus" # <-- LLM MUST GENERATE THIS DYNAMICALLY

    # 2. Retrieve the raw output from the previous step.
    raw_json_output = context.lastCommandOutput
    
    # 3. Parse the output to get the actual value.
    import json
    data = json.loads(raw_json_output)
    actual_value = data['items']['status']['phase'] # Example parsing
    
    # 4. Create a dictionary to hold the results.
    result_data = {
        "lookup_key": lookup_key,
        "actual_value": actual_value
    }
    
    # 5. Write the JSON file to the project's root directory.
    with open('test_result.json', 'w') as f:
        json.dump(result_data, f)
    # --- END 'THEN' STEP EXAMPLE PATTERN ---
    ```
4.  **IMPORTS:** List any required imports (like `import json`) at the very top of your response.

---
**Current Gherkin Step:** "{{ step_line }}"

{% if previous_step_error %}
**The last attempt failed. FIX IT based on the rules and examples above.** The error was: {{ previous_step_error }}
{% endif %}
---
Provide ONLY the raw Python code for the method body now:
""",

    "godog": """
You are a Go test automation expert using Godog. Your ONLY task is to write the Go code that goes inside a method body to implement a single Gherkin step.

**THE ROLE OF A 'Then' STEP IS TO EXTRACT DATA FOR LATER VERIFICATION.**
- For **Given/When** steps, interact with the system and store raw results in a field like `s.lastCommandOutput`.
- For **Then** steps, your job is to:
    1. Create a descriptive, unique `lookupKey` in `camelCase` from the step text.
    2. Extract the actual value from `s.lastCommandOutput`.
    3. Write a single JSON file named `test_result.json` containing BOTH the `lookupKey` and the `actualValue`.

---
**CRITICAL RULES:**
1.  **YOUR RESPONSE MUST BE ONLY THE RAW GO CODE FOR THE METHOD'S BODY.** Do not include the method signature, comments, or markdown.
2.  **'Then' STEPS MUST NOT USE `t.Errorf` or any other assertion.** They only extract data.
3.  **HOW TO WRITE THE JSON RESULT FILE (for a 'Then' step):**
    *   You MUST use the following pattern, writing the file to the root of the project.

    ```go
    // --- START 'THEN' STEP EXAMPLE PATTERN ---
    // 1. Create a dynamic lookup key from the Gherkin step.
    // For a step like "the user's name should be {string}", a good key would be "userName".
    // For "the pod status should be {string}", a good key is "podStatus".
    lookupKey := "podStatus" // <-- LLM MUST GENERATE THIS DYNAMICALLY

    // 2. Retrieve the raw output from the previous step.
    rawJSONOutput := s.lastCommandOutput
    
    // 3. Parse the output to get the actual value. (This is a simplified example)
    var result map[string]interface{}
    json.Unmarshal([]byte(rawJSONOutput), &result)
    actualValue := result["items"].([]interface{}).(map[string]interface{})["status"].(map[string]interface{})["phase"].(string)
    
    // 4. Create a map to hold the results.
    resultData := map[string]string{
        "lookup_key": lookupKey,
        "actual_value": actualValue,
    }
    
    // 5. Marshal the map to JSON and write the file.
    jsonData, _ := json.Marshal(resultData)
    os.WriteFile("test_result.json", jsonData, 0644)
    // --- END 'THEN' STEP EXAMPLE PATTERN ---
    ```
4.  **IMPORTS and STRUCT FIELDS:** List any required imports (`"encoding/json"`, `"os"`) and necessary `scenarioContext` fields at the top of your response.

---
**Current Gherkin Step:** "{{ step_line }}"

{% if previous_step_error %}
**The last attempt failed. FIX IT based on the rules and examples above.** The error was: {{ previous_step_error }}
{% endif %}
---
Provide ONLY the raw Go code for the method body now:
""",

    "cucumber": """
You are a Java test automation expert. Your ONLY task is to write the Java code that goes inside a method body to implement a single Gherkin step.

**THE ROLE OF A 'Then' STEP IS TO EXTRACT DATA FOR LATER VERIFICATION.**
- For **Given/When** steps, interact with the system and store raw results in `StepDefinitions.lastCommandOutput`.
- For **Then** steps, your job is to:
    1.  Create a descriptive, unique `lookupKey` in `camelCase` from the step text (e.g., from "the pod status should be...", create a key like `podStatus`).
    2.  Extract the actual value from `StepDefinitions.lastCommandOutput`.
    3.  Write a single JSON file named `test_result.json` containing BOTH the `lookupKey` and the `actualValue`.

---
**CRITICAL RULES:**
1.  **YOUR RESPONSE MUST BE ONLY THE RAW JAVA CODE FOR THE METHOD'S BODY.** Do not include method signatures, class definitions, annotations, comments, or markdown.
2.  **'Then' STEPS MUST NOT USE `Assert.assertEquals`.** They only extract data.
3.  **HOW TO WRITE THE JSON RESULT FILE (for a 'Then' step):**
    *   You MUST use the following pattern, writing the file to the root of the project's `target` directory.

    ```java
    // --- START 'THEN' STEP EXAMPLE PATTERN ---
    // 1. Create a dynamic lookup key from the Gherkin step.
    // For a step like "the user's name should be {string}", a good key would be "userName".
    // For "the response status code should be {int}", a good key would be "responseStatusCode".
    String lookupKey = "podStatus"; // <-- LLM MUST GENERATE THIS DYNAMICALLY

    // 2. Retrieve the raw output from the previous step.
    String rawJsonOutput = StepDefinitions.lastCommandOutput;
    
    // 3. Parse the output to get the actual value.
    ObjectMapper objectMapper = new ObjectMapper();
    JsonNode rootNode = objectMapper.readTree(rawJsonOutput);
    String actualValue = rootNode.at("/items/0/status/phase").asText();
    
    // 4. Create a Map to hold the results.
    java.util.Map<String, String> resultData = new java.util.HashMap<>();
    resultData.put("lookup_key", lookupKey);
    resultData.put("actual_value", actualValue);
    
    // 5. Write the Map as a JSON file to the 'target' directory.
    try (java.io.FileWriter writer = new java.io.FileWriter("target/test_result.json")) {
        objectMapper.writeValue(writer, resultData);
    }
    // --- END 'THEN' STEP EXAMPLE PATTERN ---
    ```
4.  **IMPORTS:** List any required imports (like `java.util.Map`, `java.io.FileWriter`, etc.) at the top of your response.

---
**Current Gherkin Step:** "{{ step_line }}"

{% if previous_step_error %}
**The last attempt failed. FIX IT based on the rules and examples above.** The error was: {{ previous_step_error }}
{% endif %}
---
Provide ONLY the raw Java code for the method body now:
"""
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
        response_message = llm.invoke(prompt)
        organized_content = response_message.content.strip()
        time.sleep(1)
        
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
        if framework == "cucumber":
            static_part = static_part.replace("/", "\\/")
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

    if framework == "cucumber":
        remaining_part = remaining_part.replace("/", "\\/")
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

def generate_step_metadata(step_text: str, framework: str, test_config: dict, scenario_content: str, full_feature_content: str, previous_step_error: str = None, context=None) -> dict:
    step_keyword_match = re.match(r'^(Given|When|Then|And|But)\s+(.*)', step_text, flags=re.IGNORECASE)
    if not step_keyword_match:
        raise ValueError(f"Invalid Gherkin step: {step_text}")
    gherkin_keyword = step_keyword_match.group(1).lower()
    if gherkin_keyword in ["and", "but"]:
        if context and "last_keyword" in context:
            gherkin_keyword = context["last_keyword"]
        else:
            gherkin_keyword = "then"
    if framework == "behave" and context is not None:
        context["last_keyword"] = gherkin_keyword
    formatted_step_text, parameters = format_step_for_framework(step_text, framework)
    step_base = re.sub(r'[^a-z0-9]+', '_', re.sub(r'"[^"]+"', '', step_text).strip().lower())
    func_name = f"{step_base}_{hashlib.md5(step_text.encode()).hexdigest()[:8]}"

    # 1. Get the raw prompt string from the dictionary.
    prompt_template_str = FRAMEWORK_LOGIC_PROMPTS[framework]
    
    # 2. Use the existing Jinja ENVIRONMENT to parse the template string.
    #    The `env` object already has your 'tojson' filter configured.
    #    This is the key fix.
    jinja_template = env.from_string(prompt_template_str)

    # 3. Use JINJA to RENDER the template into a FINAL, SIMPLE STRING.
    #    This step will now correctly process the `| tojson` filter and the `{% if ... %}` block.
    final_prompt_string = jinja_template.render(
        full_feature_content=full_feature_content,
        step_line=step_text,
        scenario_content=scenario_content,
        parameters=parameters,
        parameter_values={}, # You can add values here if needed
        test_config=test_config, # Pass the dict directly; Jinja will handle the filter
        previous_step_error=previous_step_error
    )
    
    # 4. Define and Invoke a SIMPLE LangChain Chain that does NO templating.
    chain = llm | StrOutputParser()
    try:
        # We pass the final, fully-rendered string directly to the LLM.
        llm_output = chain.invoke(final_prompt_string)
        time.sleep(1)

        # 5. Process the (already clean) output
        # The StrOutputParser handles stripping whitespace and markdown.
        # We just need to extract imports.
        import_lines_set = set()
        logic_cleaned = llm_output
        
        # This regex can find both Python/Go and Java style imports
        import_regex = r'^\s*(?:import|from)\s+[\w\s\.\*_{},;]+;?$'
        found_imports = re.findall(import_regex, logic_cleaned, re.MULTILINE)
        for imp_line in found_imports:
            clean_import = imp_line.strip()
            import_lines_set.add(clean_import)
            logic_cleaned = logic_cleaned.replace(imp_line, '')
            
        final_logic = logic_cleaned.strip()
        
        if not final_logic:
             final_logic = "pass" if framework == "behave" else "// TODO: Implement"
             if framework == "cucumber":
                final_logic = "throw new io.cucumber.java.PendingException();"

        return {
            "func_name": func_name,
            "parameters": parameters,
            "step_text": formatted_step_text,
            "logic": final_logic,
            "imports": sorted(list(import_lines_set)),
            "gherkin_keyword": gherkin_keyword
        }

    except Exception as e:
        print(f"[LangChain Error] Chain failed for step: {step_text}\nDetails: {e}")
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
        # Create directories
        behave_features_dir = Path("behave/features")
        behave_steps_dir = behave_features_dir / "steps"
        behave_features_dir.mkdir(parents=True, exist_ok=True)
        behave_steps_dir.mkdir(parents=True, exist_ok=True)

        # Write the step definitions file
        path = behave_steps_dir / "step_definitions.py"
        path.write_text(code)

        (behave_features_dir / feature_filename).write_text(feature_content)

        # Corrected file copy logic
        if config_path and user_config_filename:
            destination_config_path = behave_features_dir / user_config_filename
            # Read from source path and write to destination path
            destination_config_path.write_text(config_path.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"Copied config file to: {destination_config_path}")

        # Update environment.py to look for the config file in the correct directory
        env_py = behave_features_dir / "environment.py"
        rendered_env_template = env_template.format(user_config_filename=user_config_filename)
        env_py.write_text(rendered_env_template)

    elif framework == "godog":
        Path("godog").mkdir(parents=True, exist_ok=True)
        path = Path("godog/main_test.go")
        path.write_text(code)
        
    elif framework == "cucumber":
        base = Path("cucumber")
        stepdefs_dir = base / "src/test/java/stepdefinitions"
        runner_dir = base / "src/test/java/runner"
        features_dir = base / "src/test/resources/features"
        resources_dir = base / "src/test/resources"

        stepdefs_dir.mkdir(parents=True, exist_ok=True)
        runner_dir.mkdir(parents=True, exist_ok=True)
        features_dir.mkdir(parents=True, exist_ok=True)
        resources_dir.mkdir(parents=True, exist_ok=True)

        (stepdefs_dir / "StepDefinitions.java").write_text(code)
        (runner_dir / "TestRunner.java").write_text(cucumber_runner_template.render())
        (base / "pom.xml").write_text(pom_template.render())
        (features_dir / feature_filename).write_text(feature_content)
        
        # Copy the config file into the resources directory
        if config_path and user_config_filename:
            src_config_path = Path(config_path)
            if src_config_path.is_file():
                root_config_path = resources_dir / user_config_filename
                root_config_path.write_text(src_config_path.read_text(encoding="utf-8"), encoding="utf-8")
                print(f"Copied real config file to: {root_config_path}")
            else:
                print(f"[WARNING] Config file not found at {src_config_path}, skipping copy.")

    return feature_path

def validate_code(code, framework, config_path=None, user_config_filename=None, feature_file_path=None):
    """
    Validates generated code by separating compilation/runtime crashes from logical test failures.

    Returns a tuple (is_runnable: bool, message: str):
    - is_runnable (True): The code is valid and runnable. The agent's job is done.
      The message will indicate if tests passed or failed logically.
    - is_runnable (False): The code is flawed (syntax, compilation, or crash). The agent must fix it.
      The message will contain the specific error.
    """
    try:
        # --------------------------------------------------------------------
        # BEHAVE (PYTHON) VALIDATION
        # --------------------------------------------------------------------
        if framework == 'behave':
            with tempfile.TemporaryDirectory() as temp_dir:
                base_path = Path(temp_dir)
                features_dir = base_path / "features"
                steps_dir = features_dir / "steps"
                steps_dir.mkdir(parents=True, exist_ok=True)
                temp_py_file = steps_dir / "step_definitions.py"
                temp_py_file.write_text(code)
                env_py = features_dir / "environment.py"
                rendered_env_template = env_template.format(user_config_filename=user_config_filename)
                env_py.write_text(rendered_env_template)
                if config_path and user_config_filename and Path(config_path).exists():
                    (features_dir / user_config_filename).write_text(Path(config_path).read_text(encoding="utf-8"))
                if feature_file_path and Path(feature_file_path).exists():
                    (features_dir / feature_file_path.name).write_text(feature_file_path.read_text(encoding="utf-8"))
                else:
                    (features_dir / "temp.feature").write_text("Feature: Temp\n  Scenario: Temp\n    Given a temp step")

                # --- STAGE 1: SYNTAX CHECK ---
                try:
                    code_from_file = temp_py_file.read_text()
                    ast.parse(code_from_file)
                except SyntaxError as e:
                    return False, f"CODE_SYNTAX_FAILED: Python syntax error: {str(e)}"

                # --- STAGE 2: RUNTIME CHECK ---
                behave_cmd = ["behave", "--no-color", str(features_dir)]
                test_result = subprocess.run(
                    behave_cmd, cwd=base_path, capture_output=True, text=True, shell=False
                )
                
                # --- KEY CHANGE: Analyze Behave's Output ---
                full_output = test_result.stdout + test_result.stderr
                if test_result.returncode == 0:
                    return True, "VALIDATION_SUCCESS: Code ran and all tests passed."
                
                # If it failed, check if it was a logical assertion failure or a code crash
                if "AssertionError" in full_output:
                    # A test failed logically. This is a success for the agent.
                    return True, f"VALIDATION_SUCCESS: Code ran, but a test assertion failed as expected.\n{full_output}"
                else:
                    # Any other error is a code crash (e.g., NameError, TypeError). The agent must fix this.
                    return False, f"RUNTIME_CRASH_FAILED: Behave test execution crashed.\n{full_output}"

        # --------------------------------------------------------------------
        # GODOG (GO) VALIDATION
        # --------------------------------------------------------------------
        elif framework == 'godog':
            with tempfile.NamedTemporaryFile(delete=False, suffix=".go", mode='w', encoding='utf-8') as temp_file:
                temp_file.write(code)
                temp_file_path = temp_file.name
            try:
                # --- STAGE 1: COMPILE CHECKS ---
                for cmd in [["gofmt", "-l", temp_file_path], ["go", "vet", temp_file_path], ["go", "build", "-o", os.devnull, temp_file_path]]:
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        return False, f"CODE_COMPILATION_FAILED: Go static analysis/build failed for command '{' '.join(cmd)}': {result.stderr.strip()}"
                
                # --- STAGE 2: RUNTIME CHECK ---
                test_result = subprocess.run(["go", "test", temp_file_path], capture_output=True, text=True)
                
                # --- KEY CHANGE: Analyze Go's Output ---
                full_output = test_result.stdout + test_result.stderr
                if test_result.returncode == 0:
                    return True, "VALIDATION_SUCCESS: Code compiled and all tests passed."
                
                if "--- FAIL:" in full_output:
                    # A logical test failure. This is a success for the agent.
                    return True, f"VALIDATION_SUCCESS: Code ran, but a test failed as expected.\n{full_output}"
                else:
                    # A panic or other crash. The agent must fix this.
                    return False, f"RUNTIME_CRASH_FAILED: Go test execution crashed.\n{full_output}"
            finally:
                os.remove(temp_file_path)

        # --------------------------------------------------------------------
        # CUCUMBER (JAVA) VALIDATION
        # --------------------------------------------------------------------
        elif framework == 'cucumber':
            with tempfile.TemporaryDirectory() as temp_dir:
                # --- Setup Temporary Directory (same as before) ---
                base_path = Path(temp_dir)
                stepdefs_dir = base_path / "src/test/java/stepdefinitions"
                runner_dir = base_path / "src/test/java/runner"
                features_dir = base_path / "src/test/resources/features"
                resources_dir = base_path / "src/test/resources"

                stepdefs_dir.mkdir(parents=True, exist_ok=True)
                runner_dir.mkdir(parents=True, exist_ok=True)
                features_dir.mkdir(parents=True, exist_ok=True)
                resources_dir.mkdir(parents=True, exist_ok=True)

                (stepdefs_dir / "StepDefinitions.java").write_text(code)
                (runner_dir / "TestRunner.java").write_text(cucumber_runner_template.render())
                (base_path / "pom.xml").write_text(pom_template.render())

                if feature_file_path and Path(feature_file_path).exists():
                    (features_dir / feature_file_path.name).write_text(feature_file_path.read_text(encoding="utf-8"))
                if config_path and user_config_filename:
                    if Path(config_path).exists():
                        (resources_dir / user_config_filename).write_text(Path(config_path).read_text(encoding="utf-8"))
                    else:
                        return False, f"CODE_SETUP_FAILED: Config file {config_path} does not exist."

                # --- STAGE 1: COMPILE CHECK ---
                mvn_cmd = r"C:\Program Files\apache-maven-3.9.10\bin\mvn.cmd" # Ensure this path is correct
                compile_result = subprocess.run(
                    [mvn_cmd, "test-compile"],
                    cwd=base_path, capture_output=True, text=True, shell=False
                )
                if compile_result.returncode != 0:
                    error_output = compile_result.stdout + compile_result.stderr
                    return False, f"CODE_COMPILATION_FAILED: Maven compilation failed:\n{error_output}"
                
                # --- STAGE 2: RUNTIME CHECK ---
                test_result = subprocess.run(
                    [mvn_cmd, "test"],
                    cwd=base_path, capture_output=True, text=True, shell=False
                )
                
                # --- KEY CHANGE: Analyze Maven's Output ---
                full_output = test_result.stdout + test_result.stderr
                if test_result.returncode == 0:
                    return True, "VALIDATION_SUCCESS: Code compiled and all tests passed."
                
                # If "Failures:" or "Errors:" is in the output, the tests RAN. This is a success for the agent.
                # Note: An "Error" in Maven surefire usually means an uncaught exception (a crash),
                # but an agent should fix that. A "Failure" is an assertion error. We will treat both as
                # runnable for simplicity, but a more advanced agent could distinguish them.
                if "Failures:" in full_output or "Errors:" in full_output:
                    return True, f"VALIDATION_SUCCESS: Code compiled and tests ran. Some tests may have failed as expected.\n{full_output}"
                else:
                    # The build failed for a reason other than a test failure (e.g., plugin error, crash).
                    # This is a code issue the agent must fix.
                    return False, f"RUNTIME_CRASH_FAILED: Maven test execution failed without reporting test results.\n{full_output}"

        else:
            return False, f"Unsupported framework: {framework}"

    except Exception as e:
        return False, f"An unexpected error occurred during validation: {str(e)}"
    
# Use the @tool decorator to make your existing function available to the agent
# REPLACE your existing @tool function with this simplified version.

@tool
def validate_generated_test_code(generated_code: str) -> str:
    """
    Validates the generated BDD test code for any framework. It cleans the raw input string 
    to remove common LLM artifacts like explanatory text and markdown fences before validating.
    """
    
    # --- Universal Cleaning Logic ---
    
    # 1. Strip leading/trailing whitespace.
    cleaned_code = generated_code.strip()
    
    # 2. Find the first real line of code to strip any preceding text.
    # This list covers Python, Go, and Java start-of-code keywords.
    start_keywords = ('from ', 'import ', '@given', 'package ', '//', '/*')
    code_lines = cleaned_code.split('\n')
    start_index = 0
    for i, line in enumerate(code_lines):
        if line.strip().startswith(start_keywords):
            start_index = i
            break
    
    # 3. Rejoin the code from the first real line.
    cleaned_code = '\n'.join(code_lines[start_index:])

    # 4. Remove markdown fences (e.g., ```python ... ```) if they exist.
    if cleaned_code.startswith("```"):
        cleaned_code = re.sub(r'^```[a-zA-Z]*\n', '', cleaned_code)
    if cleaned_code.endswith("```"):
        cleaned_code = cleaned_code[:-3].strip()

    # --- End of Cleaning Logic ---
    
    # Now, pass the thoroughly cleaned code to the validation engine.
    is_valid, message = validate_code(
        code=cleaned_code,
        framework=framework_for_agent,
        config_path=config_path_for_agent,
        user_config_filename=user_config_filename_for_agent,
        feature_file_path=feature_file_path_for_agent
    )
    
    if is_valid:
        return f"VALIDATION_SUCCESS: {message}"
    else:
        # Return the specific error message for the agent to analyze
        return f"VALIDATION_FAILED: {message}"

tools = [validate_generated_test_code]

# Get a pre-built prompt template for this type of agent
prompt = hub.pull("hwchase17/react")

# Create the agent, giving it the LLM, the tools, and the prompt
agent = create_react_agent(llm, tools, prompt)

# The AgentExecutor is what actually runs the agent loop
agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True, # verbose=True lets you see the agent's thoughts
    max_iterations=5,  # Set a reasonable limit to prevent runaway costs
    handle_parsing_errors=True # This is the key fix for the OutputParserException
) 

def clean_agent_output(agent_output: str) -> str:
    """
    Parses the agent's final output string to extract only the code block.
    It handles markdown fences for any language and the "Final Answer:" prefix.
    """
    # First, check if "Final Answer:" is in the output and split by it
    if "Final Answer:" in agent_output:
        agent_output = agent_output.split("Final Answer:")[-1].strip()

    # Next, find and extract the content within the first code block ```...```
    # This regex is generic and works for ```python, ```go, ```java, etc.
    match = re.search(r'```(?:\w*\n)?(.*)```', agent_output, re.DOTALL)
    
    if match:
        # If a markdown block is found, return its content
        return match.group(1).strip()
    else:
        # If no markdown block is found, assume the whole string is the code
        return agent_output.strip()

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
    test_config = load_test_config(filename=user_config_filename, start_path=config_path.parent)

    global framework_for_agent, config_path_for_agent, user_config_filename_for_agent, feature_file_path_for_agent
    framework_for_agent = framework
    config_path_for_agent = config_path
    user_config_filename_for_agent = user_config_filename
    feature_file_path_for_agent = feature_file_path

    # Step 6: Parse Feature by Scenario and Generate Step Metadata
    scenarios_data = parse_feature_by_scenario(feature_content)
    
    all_step_metadata = []
    all_custom_imports = set()
    all_godog_fields = set() 
    context = {} 
    
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
                previous_step_error=None,  # No per-step retry currently
                context=context
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


    # Step 7: Generate and Validate Code with an Agent
    
    print("\n--- Invoking LangChain Agent to Generate and Validate Code ---")
    
    # Generate the initial "flawed" code first, outside the agent
    initial_code_to_correct = generate_framework_code(
        all_step_metadata=all_step_metadata,
        framework=framework,
        custom_imports=sorted(list(all_custom_imports)),
        scenario_context_fields=sorted(list(all_godog_fields)),
        user_config_filename=user_config_filename
    )

    # The agent's input describes its goal and gives it the context it needs.
    agent_prompt_template = Template("""
My one and only goal is to write a complete, syntactically correct, and runnable BDD test file for the "{{ framework }}" framework.

The final code MUST be a single block of code that can be saved directly to a file.
The code must follow the "extract, don't assert" pattern for 'Then' steps, writing a `test_result.json` file.

---
**FRAMEWORK-SPECIFIC INSTRUCTIONS FOR "{{ framework }}":**

{% if framework == 'behave' %}
**IMPORTANT for Behave:** The test configuration from `{{ user_config_filename }}` is automatically loaded into the `context.test_config` object. You MUST access config values from there.
- **CORRECT:** `command = context.test_config['commands']['some_command']`
- **INCORRECT:** `with open('config.yaml', 'r') as f:` <-- DO NOT DO THIS.
{% elif framework == 'cucumber' %}
**IMPORTANT for Cucumber:** The test configuration from `{{ user_config_filename }}` is automatically loaded into the static `testConfig` JsonNode. You MUST access config values using the `.at()` method.
- **CORRECT:** `String cmd = testConfig.at("/commands/get_pod_json_by_label").asText();`
- **INCORRECT:** `testConfig.get("...")` <-- DO NOT DO THIS.
{% elif framework == 'godog' %}
**IMPORTANT for Godog:** You will need to write the logic to load and parse the `config.yaml` file yourself. Store the parsed config in a field on the `scenarioContext` struct.
- **EXAMPLE:** `s.config = loadConfig("config.yaml")`
{% endif %}
---

Here is all the information you need:

1. GHERKIN FEATURE FILE:
---
{{ feature_content }}
---

2. CONFIGURATION FILE (`{{ user_config_filename }}`):
---
{{ test_config_json }}
---

3. INITIAL DRAFT of the step definition code that I must fix:
---
{{ initial_code_to_correct }}
---

My process is as follows:
1.  Write the complete, corrected code based on all instructions.
2.  Use the `validate_generated_test_code` tool to check the code.
3.  If the tool reports `VALIDATION_FAILED`, I will revise my code and try again.
4.  If the tool reports `VALIDATION_SUCCESS`, my job is done.

My final answer must be ONLY the complete, corrected code that was successfully validated.
""")

    agent_input = agent_prompt_template.render(
        framework=framework,
        user_config_filename=user_config_filename,
        feature_content=feature_content,
        test_config_json=json.dumps(test_config, indent=2),
        initial_code_to_correct=initial_code_to_correct
    )

    # Invoke the agent executor
    result = agent_executor.invoke({
        "input": agent_input
    })

    # Get the raw output from the agent
    agent_raw_output = result['output']

    # --- THIS IS THE CRITICAL FIX ---
    # Use the universal cleaning function to extract the pure code.
    final_generated_code = clean_agent_output(agent_raw_output)
    
    # Check if the agent's final code is actually runnable. This is a safety check.
    is_valid_runnable_code, validation_message = validate_code(final_generated_code, framework, config_path, user_config_filename, feature_file_path)

    if not is_valid_runnable_code:
        print("\n!!! LangChain Agent FAILED to produce runnable code. This is an agent failure. !!!")
        print("Final error from validation tool:\n" + validation_message)
        # Write the flawed code so user can inspect
        write_code(framework, feature_content, final_generated_code, feature_filename)
        print(f"Generated code (with errors) saved for inspection.")
        return # Exit
    
    print("\n[✓] LangChain Agent successfully generated runnable code.")
        
    # Step 8: Write the final, validated code to the project structure
    print("Writing generated code to project structure...")
    write_code(framework, feature_content, final_generated_code, feature_filename, config_path, user_config_filename)

    # Step 9: Execute Data Extraction Run for the Target Framework
    print(f"\n--- EXECUTING DATA EXTRACTION RUN FOR {framework.upper()} ---")

    # Define project paths and the result file that the generated code will create
    project_dir = Path(framework)
    result_file_path = project_dir / "test_result.json"
    if framework == "cucumber":
        result_file_path = project_dir / "target" / "test_result.json"
    
    # Clean previous results before running
    if result_file_path.exists():
        result_file_path.unlink()

    # --- Framework-specific execution command ---
    execution_result = None
    if framework == "behave":
        # Behave's working directory is the project root (e.g., the 'behave' folder)
        execution_result = subprocess.run(
            ["behave"], cwd=project_dir, capture_output=True, text=True
        )
    elif framework == "godog":
        execution_result = subprocess.run(
            ["go", "test", "./..."], cwd=project_dir, capture_output=True, text=True
        )
    elif framework == "cucumber":
        mvn_cmd = r"C:\Program Files\apache-maven-3.9.10\bin\mvn.cmd" # Ensure this path is correct
        execution_result = subprocess.run(
            [mvn_cmd, "clean", "test"], cwd=project_dir, capture_output=True, text=True, shell=False
        )
    
    # --- Check for crashes during the extraction run ---
    # Note: A non-zero exit code from a test runner can mean a crash OR a failed assertion.
    # Since our generated code no longer has assertions, any failure here is a true crash.
    if execution_result.returncode != 0:
        print(f"\n[X] CRITICAL ERROR: The {framework} data extraction code crashed during execution.")
        print("--- STDOUT ---")
        print(execution_result.stdout)
        print("\n--- STDERR ---")
        print(execution_result.stderr)
        return # Stop execution

    print("\n[✓] Data extraction run completed successfully.")
    print(execution_result.stdout) # Print the successful run output

    # Step 10: Perform Dynamic Assertion in Python (The Final Verdict)
    print("\n--- PERFORMING DYNAMIC ASSERTION IN PYTHON ---")
    try:
        if not result_file_path.exists():
             raise FileNotFoundError(f"The result file '{result_file_path}' was not created. The '@Then' step likely failed or was not executed.")
        
        with open(result_file_path, 'r') as f:
            result_data = json.load(f)
        
        lookup_key = result_data.get("lookup_key")
        actual_value = result_data.get("actual_value")

        if lookup_key is None or actual_value is None:
            raise ValueError("Result JSON is malformed. It must contain 'lookup_key' and 'actual_value'.")

        expected_value = test_config.get("expected_outputs", {}).get(lookup_key)

        if expected_value is None:
            raise KeyError(f"The lookup key '{lookup_key}' from test_result.json was not found in the 'expected_outputs' section of your config file.")

        print(f"  - Assertion Key:   '{lookup_key}'")
        print(f"  - Actual Value:    '{actual_value}'")
        print(f"  - Expected Value:  '{expected_value}'")
        
        # Perform the final comparison
        if str(actual_value) == str(expected_value):
            print("\n[SUCCESS] FINAL TEST STATUS: PASSED")
        else:
            print(f"\n[FAILURE] FINAL TEST STATUS: FAILED. Expected '{expected_value}' but got '{actual_value}'.")

    except Exception as e:
        print(f"\n[X] CRITICAL ERROR: Could not perform final assertion in Python. Reason: {e}")

if __name__ == '__main__':
    main()