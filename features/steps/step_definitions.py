from behave import given, when, then

import json

import os

import subprocess



@given('the mini Kube cluster is accessible')
def step_impl(context):
    subprocess.run(["minikube", "status"], check=True)
    context.kubeconfig_path = os.path.expanduser("~/.kube/config")
    context.default_namespace = "default"
    context.cluster_context_name = "minikube"
    context.available_context = {}
    context.available_context["cluster_context_name"] = context.cluster_context_name
    context.available_context["kubeconfig_path"] = context.kubeconfig_path
    context.available_context["default_namespace"] = context.default_namespace

@when('I check the status of the pod with label {label}')
def step_impl(context, label):
    label = label.strip('"')
    context.pod_status = subprocess.check_output(f"kubectl --kubeconfig {context.kubeconfig_path} --context {context.cluster_context_name} -n {context.default_namespace} get pod -l {label} -o json", shell=True)
    context.pod_status_json = json.loads(context.pod_status.decode('utf-8'))

@then('the pod status should be {status}')
def step_impl(context, status):
    status = status.strip('"')
    expected_status_value = status
    actual_status = context.pod_status_json['items'][0]['status']['phase']

    if actual_status != expected_status_value:
        pod_status_json = context.pod_status_json
        print(f"Expected pod status to be {expected_status_value} but got {actual_status}. Pod status JSON: {pod_status_json}")
        assert False, f"Expected pod status to be {expected_status_value} but got {actual_status}"