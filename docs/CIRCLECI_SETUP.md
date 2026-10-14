# CircleCI setup: JobPunter backend

## Purpose

CircleCI is the intended primary CI provider for the private
`GoReallyBig-GRB/jobpunter-backend` repository. The CircleCI pipeline is designed to
preserve the existing backend verification gates in
`.github/workflows/verify.yml`: Python compilation, one Alembic migration head,
PostgreSQL 17 migration upgrade, ORM/schema parity, and unit plus PostgreSQL
integration tests.

## One-time account setup

1. Sign in to CircleCI using the GitHub account or organization that has access to
   `GoReallyBig-GRB/jobpunter-backend`.
2. Authorize the CircleCI GitHub App / repository access when prompted.
3. Add or set up the `GoReallyBig-GRB/jobpunter-backend` project.
4. Use the repository's `.circleci/config.yml` as the pipeline configuration.
5. Trigger a build for the CircleCI integration pull request and inspect the complete
   result, including the exact commit SHA.
6. Confirm that a push to the working branch and a pull-request update both trigger
   the expected pipeline.

The CircleCI project must be connected and authorized in CircleCI's UI. A config
file committed to GitHub does not itself authorize CircleCI to access a private
repository or prove that the remote pipeline ran.

## Cutover gates

Do not disable GitHub Actions or change required branch-protection checks merely
because this configuration exists.

Before making CircleCI the required primary check:

- The pipeline passes on the integration PR's latest head SHA.
- It also passes on a representative backend change that exercises PostgreSQL 17
  migrations and integration tests.
- The reported test counts and failures are inspected, not inferred from a green
  badge alone.
- CircleCI usage and credits are checked in the account after actual runs.
- The required status-check name is confirmed from GitHub's reported check.
- Any duplicate GitHub Actions triggers are disabled only after CircleCI is proven
  reliable and the replacement required check is configured.

## Cost controls

The job requests CircleCI's `small` Docker resource class and runs one verification
job rather than parallel duplicate jobs. Pip's download cache is keyed to
`pyproject.toml`. Recheck the actual credit consumption in CircleCI after the
first complete run; estimates are not a guarantee of the account's charge rate.

No paid plan, automatic credit refill, deployment, or repository visibility change
is configured by this file.
