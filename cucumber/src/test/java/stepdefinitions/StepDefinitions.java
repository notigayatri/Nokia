package stepdefinitions;
import io.cucumber.java.en.Given;
import io.cucumber.java.en.When;
import io.cucumber.java.en.Then;
import org.junit.Assert;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;

public class StepDefinitions {

    @Given("the mini Kube cluster is up and reachable")
    public void given_the_mini_kube_cluster_is_up_and_reachable() {
        ProcessBuilder processBuilder = new ProcessBuilder("cmd", "/c", "minikube status");
        processBuilder.redirectErrorStream(true);
        try {
            Process process = processBuilder.start();
            BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
            StringBuilder output = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) {
                output.append(line);
            }
            reader.close();
            process.waitFor();
            String statusOutput = output.toString();
            Assert.assertTrue("Mini Kube cluster is not up and reachable", statusOutput.contains("host: Running") && statusOutput.contains("kubelet: Running"));
        } catch (IOException | InterruptedException e) {
            throw new RuntimeException("Failed to check mini Kube cluster status", e);
        }
    }

    @When("I request the current operational status of the pod with label {string}")
    public void when_i_request_the_current_operational_status_of_the_pod_with_label(String label) {
        ProcessBuilder processBuilder = new ProcessBuilder("cmd.exe", "/c", "kubectl get pods -l " + label);
        try {
            Process process = processBuilder.start();
            BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
            String line;
            StringBuilder output = new StringBuilder();
            while ((line = reader.readLine()) != null) {
                output.append(line);
            }
            int exitVal = process.waitFor();
            if (exitVal == 0) {
                String[] podStatus = output.toString().split("\\s+");
                if (podStatus.length > 2) {
                    String status = podStatus[2];
                    System.out.println("Pod status: " + status);
                }
            } else {
                System.out.println("Failed to get pod status");
            }
        } catch (IOException | InterruptedException e) {
            System.out.println("Error occurred: " + e.getMessage());
        }
    }

    @Then("the returned status for this pod must be {string}")
    public void then_the_returned_status_for_this_pod_must_be(String status) {
        ProcessBuilder processBuilder = new ProcessBuilder("cmd.exe", "/c", "kubectl get pods -l app=flask-api -o jsonpath=\"{.items[0].status.phase}\"");
        processBuilder.redirectErrorStream(true);
        try {
            Process process = processBuilder.start();
            BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
            String line;
            StringBuilder output = new StringBuilder();
            while ((line = reader.readLine()) != null) {
                output.append(line);
            }
            reader.close();
            process.waitFor();
            String returnedStatus = output.toString().trim();
            Assert.assertEquals(status, returnedStatus);
        } catch (IOException | InterruptedException e) {
            throw new RuntimeException("Error occurred while getting pod status", e);
        }
    }
}