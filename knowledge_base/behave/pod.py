from behave import given, when, then
import json, os, subprocess, shlex

@given('the mini Kube cluster is accessible')
def step_given_minikube_accessible(context):
    cmd = context.test_config['commands']['get_minikube_status']
    result = subprocess.run(shlex.split(cmd), capture_output=True, text=True)
    context.lastCommandOutput = result.stdout.strip()
    context.lastCommandStatusCode = result.returncode

@when('I check the status of the pod with label "{label}"')
def step_when_check_pod_status(context, label):
    cmd_template = context.test_config["commands"]["get_pod_stats"]
    namespace = context.test_config["environment"].get("default_namespace", "default")
    cmd = cmd_template.format(label=label, namespace=namespace)
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    context.lastCommandOutput = result.stdout.strip()
    context.lastCommandStatusCode = result.returncode

@then('the pod status should be "{expected_status}"')
def step_then_pod_status(context, expected_status):
    actual_status = getattr(context, "lastCommandOutput", "")
    result_entry = {
        "lookup_key": "podStatusRunning",
        "expected_value": expected_status,
        "actual_value": actual_status
    }
    result_file = "test_result.json"
    if os.path.exists(result_file):
        try:
            with open(result_file, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            existing = []
    else:
        existing = []
    existing.append(result_entry)
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)