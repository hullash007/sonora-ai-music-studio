# UI01-04 public preview release

Verified live2026-11-15 23:50 IST.

Preview:https://hullasho7--sonora-staging-music-portal.us-east.modal.run/

Actions:https://github.com/hullash007/sonora-ai-music-studio/actions/runs/43745823633

- Remote checkout2357b7e1e2a8b4b9eea28631612b445877245118. Workflow expected and actual SHA exactly match.
- Tree7ecc9b67e7b08e759892a5068e0302dc36ee6ddb matches local4c73671 exactly, preserving local increment commits.
- Immutable claimsonora:deployment-validation:2026-11-15:ui04-rc1-273ab851bf3f. Prior claim history preserved.
- Workflow succeeded12seconds, deploy1.576seconds. Actual portal verified separately.
- Homepage, health, library, assets200; library empty. JS/CSS exactly match release candidate hashes.
- HTML response injects public-preview configuration; response hash differs from source by design.
- All UI02/03/04 live interaction suites plus clean-profile/keyboard suite pass at1440/1024/768/390/320. Includes draft/project migration/persistence, manual prompts/templates/reference handling, API contract Unicode limits, JSON copy and blocked-clipboard fallback, dialog focus/Escape and jump focus.
- No browser JS errors, horizontal overflow, external or mutation requests from UI. No password input or seeded test data in clean profiles.
-65 separate live route/method/traversal probes:public GET/HEAD200, hidden/unknown404, mutations403. No accepted generation request.
- Actual live desktop/mobile screenshots inspected, plus cloud-browser mobile. Short viewport simulations are not physical keyboard tests.
- Generation hardOFF. No GPU execution, model initialization/download or production changes requested.
- Token sonora-ui04-rc1 revoked. Modal API tokens0 verified. Temporary repository token ID/secret deleted; repository secrets empty verified.
- Meter before1.688094917505119; after1.6930411857387493; delta0.0049462682336303 included credits. Rounded$1.69/$0charges. Includes verification traffic, may lag.

## Scope still explicit

Public preview is a local composition workspace and read-only library, not a working music generation service. Drafts/projects live in the current browser, not cloud sync. Preflight is deterministic input validation, not an engine quality guarantee. No generated tracks or fake credits/accounts are shown.
