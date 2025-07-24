from behave import given, when, then

import json
import subprocess

@given('the mini Kube cluster is accessible')
def step_impl(context):
    subprocess.run(['minikube', 'status'], check=True)
    context.cluster_accessible = True
    context.kubeconfig_path = None
    context.default_namespace = None 
    context.pod_status_json_example = None
    context.service_status_json_example = None
    context.cluster_context_name = "minikube"
    context.resource_type = None
    context.resource_name = None
    context.namespace = None
    context.kubectl_command_suffix = None
    context.expected_status_field = None
    context.expected_status_value = None
    context.yaml_file_path = None
    context.kubernetes_client_library = "subprocess"
    context.check_logs_for_string = None
    context.available_context = {} 

    output = subprocess.check_output(['minikube', 'status']).decode('utf-8')
    if 'host: Running' in output:
        context.minikube_status = 'Running'
    else:
        context.minikube_status = 'Not Running'

    if context.minikube_status == 'Running':
        subprocess.run(['kubectl', 'get', 'pods'], check=True)

@when('I check the status of the pod with label {label}')
def step_impl(context, label):
    label = label.strip('"')
    context.pod_status_json = subprocess.check_output(f"kubectl get pods -l {label} -o json", shell=True)
    context.pod_status = json.loads(context.pod_status_json.decode('utf-8'))

@then('the pod status should be {status}')
def step_impl(context, status):
    status = status.strip('"')
    pod_status = context.pod_status
    items = pod_status.get('items', [])
    if items:
        pod_status_value = items[0].get('status', {}).get('phase')
    else:
        pod_status_value = None
    assert pod_status_value == status, f"Pod status is {pod_status_value}, expected {status}"