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

Integration tests require a working Kubernetes cluster (via `microk8s`) and a Juju controller. We **assume** you already have these set up. If not, refer to the official [microk8s](https://microk8s.io/) and [juju](https://juju.is/) documentation.

**Important things to know before running the integration tests:**

* These tests require AWS-style S3 credentials.
* If you're running in a CI environment (`CI=true`), a local RadosGW (via `microceph`) is set up automatically.
* When testing locally, you **must** provide your own credentials or reuse those from `microceph`.

#### 1. Setup RadosGW

The integration tests can install microceph and RadosGW, and then run the tests. To do so you'll need to run the integration tests with `CI=true`.

When the `CI=true` environment variable is set:

* MicroCeph will be installed and bootstrapped
* A local RadosGW instance is created
* S3 credentials are generated for a user `test`
* A `testbucket` bucket is created
* The integration tests will use this local setup

> **Note**: You can create your own S3 bucket and credentials if you prefer. Just ensure the `AWS_*` environment variables are set correctly.
>
> **Note**: RadosGW will be exposed under `$(hostname):7480`

```bash
CI=true tox -vve integration -- --model velero-testing
```

#### 2. Reuse Local RadosGW

You can also run the integration tests and point them to an existing S3 to be used. This can also be the RadosGW created with `CI=true`.
For example, set the following environment variables, before running the tests:

```bash
export AWS_ENDPOINT="http://$(hostname):7480"
export AWS_REGION=radosgw
export AWS_S3_URI_STYLE=path
export AWS_BUCKET=testbucket
export AWS_SECRET_KEY=$(sudo microceph.radosgw-admin user info --uid test \
        | jq -r ".keys[0].secret_key")
export AWS_ACCESS_KEY=$(sudo microceph.radosgw-admin user info --uid test \
        | jq -r ".keys[0].access_key")
```

> **Note**: `AWS_S3_URI_STYLE` is optional, unless you are using local S3(must be set to `path`)
>
> **Note**: `AWS_ENDPOINT` is optional, unless you are using local S3

Then you can run the integration tests with:

```bash
tox -vve integration -- --model velero-testing
```
