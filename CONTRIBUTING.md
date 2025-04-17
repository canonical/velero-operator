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

- **Testing and Code Coverage**: Changes must be accompanied by appropriate unit tests and meet the project's code coverage requirements. Functional and integration tests should be added when applicable to ensure the stability of the codebase.

- **Sign Your Commits**: Be sure to [sign your commits](https://docs.github.com/en/authentication/managing-commit-signature-verification/signing-commits), refer to the [Prerequisites](#prerequisites) section.

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

#### 1. Required Environment Variables

Set the following env vars **before** running the integration tests:

```bash
export AWS_ACCESS_KEY=your_access_key_id
export AWS_SECRET_KEY=your_secret_access_key
export AWS_BUCKET=your_s3_bucket_name
export AWS_REGION=your_aws_region  # Optional unless using AWS
export AWS_ENDPOINT=http://localhost:7480  # Optional, required for local S3 (e.g. microceph)
export AWS_S3_URI_STYLE=path  # Optional, required for local S3
```

#### 2. CI Behavior

When the `CI=true` environment variable is set:

* MicroCeph is installed and bootstrapped automatically
* A local RadosGW instance is created
* S3 credentials are generated
* A test bucket is created
* The integration tests use this local setup

> **Note**: You can create your own S3 bucket and credentials if you prefer. Just ensure the `AWS_*` environment variables are set correctly.

#### 4. Run Integration Tests

Once your environment is ready:

```bash
tox -vve integration -- --model testing
```
