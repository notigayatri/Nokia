Feature: User Login

Scenario: Successful login with valid credentials
    Given the user provides username "admin" and password "secret"
    When the user tries to log in
    Then the login should be successful
