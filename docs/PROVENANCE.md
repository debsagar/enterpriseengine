# Provenance and limits

This release packages the completed Phase 1 datagen v2 working copy at source commit `0e2d52c`. The source builds on an inherited catalog/role model and service-stack support from the earlier Norvale implementation. Only support files used by v2 are included. No history containing runtime artifacts is imported into this repository.

Packaging changes make imports repository-relative, use explicit environment credentials, remove host-specific `/data` restrictions, and allow configuring the fake-clock library directory. The completed generation was not rerun or retroactively changed. Hashes in `data/manifest.json` identify the exact published JSON files.

The source run spans 120 days ending April 30, 2027. The saved summary records 258 orders, 328 production jobs, 70 JEV calls and 105 model decisions. Nine checks in `data/checks.json` passed in the completed live run. These checks constrain consistency of this trajectory, not general economic realism or empirical behavioral validity.

- Regimes are seeded persistent episodes, not empirically calibrated shocks.
- Work proceeds in daily stages, with modeled shift labor rather than minute-level scheduling.
- QA readings are nominal passing values; payroll is aggregate expense accounting.
- Some demand and work remains open at cutoff.
- Gitea uses a controller API identity and real server timestamps; issue text carries simulated business dates.
- Approval, master-data implementation, and physical outcomes are distinct events.
- Policy-unmatched decisions bypassed JEV until the correction effective February 4, 2027 (day 34). Earlier decisions remain unchanged. Later uncached cases use JEV; no standing-policy executions occurred in the delivered trajectory.
- Provider-reported usage and cost are retained in the summary. Missing cost information is unknown, not free.
- This release includes business exports, not restorable application snapshots. It excludes private causal/runtime references and raw traces; the reference builder needs those local files for new runs.

The original local repositories, services, run evidence, and separately deployed Sites presentation were preserved during packaging.
