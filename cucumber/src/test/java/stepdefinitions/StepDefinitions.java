package stepdefinitions;
import io.cucumber.java.en.Given;
import io.cucumber.java.en.When;
import io.cucumber.java.en.Then;
import org.junit.Assert;
import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;

public class StepDefinitions {

    @Given("the mini Kube cluster is accessible")
    public void given_the_mini_kube_cluster_is_accessible() {
        ProcessBuilder processBuilder = new ProcessBuilder();
        processBuilder.command("minikube", "status");
        try {
            Process process = processBuilder.start();
            BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.contains("host: Running")) {
                    break;
                }
            }
            reader.close();
            process.waitFor();
        } catch (IOException | InterruptedException e) {
            throw new RuntimeException("Failed to check minikube status", e);
        }
    }

    @When("I check the status of the pod with label {string}")
    public void when_i_check_the_status_of_the_pod_with_label(String label) {
        ProcessBuilder processBuilder = new ProcessBuilder();
        String podStatus = "";

        processBuilder.command("bash", "-c", "kubectl get pods -l " + label);
        try {
            Process process = processBuilder.start();
            BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.contains("flask-api")) {
                    String[] status = line.split("\\s+");
                    podStatus = status[status.length - 1];
                }
            }
            reader.close();
            process.waitFor();
        } catch (IOException | InterruptedException e) {
            throw new RuntimeException(e);
        }
    }

    @Then("the pod status should be {string}")
    public void then_the_pod_status_should_be(String expectedStatus) {
        String podStatus = "";

        ProcessBuilder processBuilder = new ProcessBuilder();
        try {
            processBuilder.command("kubectl", "get", "pods", "-l", "app=flask-api");
            Process process = processBuilder.start();
            BufferedReader reader = new BufferedReader(new InputStreamReader(process.getInputStream()));
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.contains("flask-api")) {
                    String[] status = line.split("\\s+");
                    podStatus = status[2];
                }
            }
            reader.close();
            process.waitFor();
        } catch (IOException | InterruptedException e) {
            throw new RuntimeException(e);
        }
        Assert.assertEquals(expectedStatus, podStatus);
    }
}