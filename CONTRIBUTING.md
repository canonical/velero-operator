# Contributor Guide

Thank you for your interest in helping us improve this project! We're open to
community contributions, suggestions, fixes, and feedback. This documentation
will assist you in navigating through our processes.

Make sure to review this guide thoroughly before beginning your contribution. It
provides all the necessary details to increase the likelihood of your contribution
being accepted.

This project is hosted and managed on [GitHub](https://github.com). If you're new to GitHub
and not familiar with how it works, their
[quickstart documentation](https://docs.github.com/en/get-started/quickstart)
provides an excellent introduction to all the tools and processes you'll need
to know.

## Prerequisites

Before you can begin, you will need to:

* Read and agree to abide by our
  [Code of Conduct](https://ubuntu.com/community/code-of-conduct).

* Sign the Canonical
  [contributor license agreement](https://ubuntu.com/legal/contributors). This
  grants us your permission to use your contributions in the project.

* Create (or have) a GitHub account.

* If you're working in a local environment, it's important to create a signing
  key, typically using GPG or SSH, and register it in your GitHub account to
  verify the origin of your code changes. For instructions on setting this up,
  please refer to
  [Managing commit signature verification](https://docs.github.com/en/authentication/managing-commit-signature-verification).

## Contributing Code

### Workflow

1. **Choose/Create an Issue**: Before starting work on an enhancement, create an issue that explains your use case. This helps track progress and keeps the discussion organized. The issue will be tracked on the GitHub issue page.

2. **Fork the Repository**: Create a fork of the repository to make your changes.

3. **Create a New Branch**: Make sure to create a new branch for your contribution.

4. **Commit your changes**: Commit messages should be well-structured and provide a meaningful explanation of the changes made

5. **Submit a Pull Request**: Submit a pull request to merge your changes into the main branch. Reference the issue by adding issue link or `Fixes: #xxx` (replace `xxx` with the issue number) to automatically link the issue to your PR.

6. **Review Process**: A team member will review your pull request. They may suggest changes or leave comments, so keep an eye on the PR status and be ready to make updates if needed.

7. **Documentation**: Any documentation changes should be included as part of your PR or as a separate PR linked to your original PR.

### Hard Requirements

* **Testing and Code Coverage**: Changes must be accompanied by appropriate unit tests and meet the project's code coverage requirements. Functional and integration tests should be added when applicable to ensure the stability of the codebase.

* **Sign Your Commits**: Be sure to [sign your commits](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits), refer to the [Prerequisites](#prerequisites) section.

## Code of Conduct

This project follows the Ubuntu Code of Conduct. You can read it in full [here](https://ubuntu.com/community/code-of-conduct).

## Testing

We support both **unit tests** and **integration tests**. Here's how to run them locally.

### Unit Tests

Ensure all unit tests pass before submitting your pull request:

```bash
tox -e unit
```

### Integration Tests

Integration tests require a working Kubernetes cluster (via `Canonical K8s`) and a Juju controller. We **assume** you already have these set up. If not, refer to the official [Canonical K8s](https://documentation.ubuntu.com/canonical-kubernetes/release-1.32/) and [juju](https://juju.is/) documentation.

**Other dependencies**

The tests require an AWS-style S3 and Azure Storage local service, to run tests locally.

For S3 we are using a local RadosGW (via `microceph`) snap and a local Azurite instance. You can either let the tests install those dependencies, or re-use them if they are already installed
from a previous run.

#### Let the tests install the dependencies

The integration tests can install microceph with RadosGW and Azurite, and then run the tests. To do so you'll need to run the integration tests with `CI=true`.
This can be used either when running the integration tests locally for the first time, or when run in the CI.

When the `CI=true` environment variable is set:

* MicroCeph will be installed and bootstrapped
* A local RadosGW instance is created
* S3 credentials are generated for a user `test`
* A `testbucket` bucket is created
* A local Azurite instance is started on port `10000`
* The integration tests will use this local setup


```bash
CI=true tox -vve integration -- --model velero-testing
```

> **Note**: You can create your own S3 bucket and credentials if you prefer. Just ensure the `AWS_*` environment variables are set correctly.
>
> **Note**: RadosGW will be exposed under `$(hostname):7480`
>
> **Note**: Azurite will be exposed under `$(hostname):10000`

#### Reuse Local RadosGW and Azurite

In case you already have RadosGW and Azurite setup, i.e. from a previous test run with `CI=true`, then you can tell the tests to use the existing services.

To do so you need to set the following environment variables:

```bash
export AWS_ENDPOINT="http://$(hostname):7480"
export AWS_REGION=radosgw
export AWS_S3_URI_STYLE=path
export AWS_BUCKET=testbucket
export AWS_SECRET_KEY=$(sudo microceph.radosgw-admin user info --uid test \
        | jq -r ".keys[0].secret_key")
export AWS_ACCESS_KEY=$(sudo microceph.radosgw-admin user info --uid test \
        | jq -r ".keys[0].access_key")

export AZURE_STORAGE_ACCOUNT=devstoreaccount1
export AZURE_CONTAINER=testcontainer
export AZURE_RESOURCE_GROUP=velero-testing
export AZURE_SECRET_KEY=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==
export AZURE_ENDPOINT="http://$(hostname):10000/devstoreaccount1"
```

> **Note**: `AWS_S3_URI_STYLE` is optional, unless you are using local S3 (must be set to `path`)
>
> **Note**: `AWS_ENDPOINT` and `AZURE_STORAGE_ENDPOINT` are optional, unless you are using local S3/Azurite

Then you can run the integration tests with:

```bash
tox -vve integration -- --model velero-testing
```
