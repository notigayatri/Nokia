from behave import given, when, then

import json
import requests

@given('api base url {url}')
def step_impl(context, url):
    context.api_base_url = url.strip('"')
    context.api_client = requests.Session()

@when('I send a GET request to {to}')
def step_impl(context, to):
    to = to.strip('"')
    context.api_client.headers.update({'Content-Type': 'application/json'})
    response = context.api_client.get(context.api_base_url + to)
    context.response = response

@then('the response status code should be {status_code}')
def step_impl(context, status_code):
    assert context.response.status_code == int(status_code)

@then('the response should contain {contain}')
def step_impl(context, contain):
    contain = contain.strip('"')
    assert contain in json.dumps(context.response.json())