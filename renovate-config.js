// Global config for the central Renovate runner.
// This file lives in infralicious/renovate-config only.
// That repo also holds the shared preset.
// It does not replace a target repo config.
// A target repo config still wins on overlap.
module.exports = {
  // Run Renovate on org repos only.
  platform: "github",
  autodiscover: true,
  autodiscoverFilter: ["infralicious/.github"],

  // Skip onboarding PRs in target repos.
  // The runner config acts as the base config.
  onboarding: false,
  requireConfig: "optional",

  // Use the shared preset from renovate-config.
  // That preset adds best practices and vulnerability alerts.
  extends: ["local>infralicious/renovate-config"],

  // Use Chicago time for schedules.
  timezone: "America/Chicago",

  // Run updates before 3am on Monday, Chicago time.
  // The workflow must also run in that window.
  schedule: ["before 3am on Monday"],

  // Use a distinct branch prefix.
  // It avoids a clash with the Mend app prefix.
  branchPrefix: "renovate-central/",

  // Open a dashboard issue in each target repo.
  dependencyDashboard: true,

  // Limit concurrent PRs on the first org-wide run.
  prConcurrentLimit: 5,

  // Test mode only. Active for the .github dry run.
  // It logs planned updates and opens no PRs.
  dryRun: "lookup",
};
