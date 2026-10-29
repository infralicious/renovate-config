# Renovate Config

## Shared preset

This repo holds the shared Renovate preset in default.json.
Target repos extend it with local>infralicious/renovate-config.
A target repo config still wins on overlap.

## Central runner

This repo also runs Renovate for the full org.
The runner lives in .github/workflows/renovate.yml.
The runner config lives in renovate-config.js.
The runner uses the infralicious-renovate GitHub App.
The App token scopes to the infralicious org.
The runner scans all repos in infralicious.
The runner opens no onboarding PRs in target repos.
The runner uses branch prefix renovate-central/.
The runner opens a dashboard issue in each target repo.
The runner limits concurrent PRs to 5 on each run.
The runner runs each Monday at 07:00 UTC.
That time is 01:00 CST and 02:00 CDT in Chicago.
The runner also runs on manual dispatch.
The Mend hosted Renovate app must stay uninstalled.
Two runners on one repo create duplicate PRs and dashboards.

## References

- https://github.com/search?q=renovate-config&type=repositories&s=stars&o=desc
