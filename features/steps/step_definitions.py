from behave import given, when, then

import json
import subprocess

@given('the mini Kube cluster is up and reachable')
def step_impl(context):
    subprocess.run(["minikube", "status"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    context.minikube_status = subprocess.check_output(["minikube", "status"]).decode('utf-8')
    if "Running" in context.minikube_status and "minikube" in context.minikube_status:
        context.minikube_up = True
    else:
        context.minikube_up = False
    assert context.minikube_up == True, "Minikube is not running"

@when('I request the current operational status of the pod with label {label}')
def step_impl(context, label):
    label = label.strip('"')
    command = f"kubectl get pods -l {label} -o json"
    result = subprocess.run(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode == 0:
        output = json.loads(result.stdout.decode('utf-8'))
        context.pod_status = output['items'][0]['status']['phase']
    else:
        context.error = result.stderr.decode('utf-8')

@then('the returned status for this pod must be {status}')
def step_impl(context, status):
    status = status.strip('"')
    assert context.pod_status == status, f"Expected pod status to be '{status}' but got '{context.pod_status}'"