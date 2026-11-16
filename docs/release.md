# Public repository handoff

Use a separate repository named `guard-connector-sdk`. Its root is this SDK checkout. The source package uses MPL-2.0. Keep the license text with distributions and use the existing SPDX header style for source files.

1. Confirm that QubitOceans has rights to publish the exported files and that dependencies and bundled assets have appropriate notices. The Python SDK has no third-party runtime dependencies. The build uses setuptools.
2. Review the file list in the release commit. Include the SDK, tests, synthetic examples, documentation, license and CI. Keep customer fixtures, local test reports and credentials in private workspaces. Verify the wheel and clean-checkout tests.
3. Create the GitHub repository under the authorized QubitOceans organization. Configure the protected `main` branch, required CI, private vulnerability reporting and maintainer review before opening contributions.
4. Attach this Git repository to the approved remote and push the reviewed initial commit. Tag `v0.1.0` after the CI checks pass on the public repository.

The release contract is [documented here](contract.md). Maintainers can accept independent connector examples with reviewed scope and fixture-based verification. Confirm live Guard compatibility separately for each newly supported source profile.
