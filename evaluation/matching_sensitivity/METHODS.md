# Controlled matching-sensitivity analysis: methods

## Comparison and scope
Each row represents a system/task pair. The patch selected for that row, repository snapshot, patch application, linter configuration, and resulting baseline/post alarm lists are held fixed. Only the L3 rule for identifying a new warning is changed. L1/L2 outcomes are held fixed. This is post-hoc sensitivity analysis, not a rerun of repair generation or retries with LOC.

`paper_recorded_*` fields preserve primary outcomes synchronized with the current repository reports. P1/P2/P3 use source A; exact mappings and source hashes are in `primary_record_sources.json`. The P1 pairing used for HapRepair is joined directly from the same source A. `verdict_*` fields are controlled offline outcomes; the two groups must not be substituted for one another. The P1 controlled offline DA accepts 648/741 whereas its recorded primary result is 630/741. Primary Moatless three-attempt records are synchronized with `../moatless_three_attempts/`, without changing any controlled matcher verdict.

`mapping.json` defines the 12 single-attempt systems, Table III systems, additional configurations, and external protocols. Claims restricted to Table III do not include additional ablations. Coverage and exclusions are explicit in `coverage_status`, `verdict_basis`, and `not_evaluated_reason`. There are 584 no-patch failures and seven external OH-MAS tasks not evaluated. External OH-MAS verdict counts of 88/93 (DA) and 86/93 (LOC) describe that evaluated subset, not the primary 89/100 result.

## Application and scans
The controlled evaluation uses `git apply --ignore-space-change --ignore-whitespace`. Linters are Cppcheck 2.17.1 and CodeLinter 6.0.240, in the documented `harmonyrepair:with-codelinter` image (`f825fefc...`). Original generated, normalized, and selected patch sources are distinguished in `patch_manifest.csv` and `patch_selection_rules.md`. Failed-run selection is a convention recorded there, rather than proof of the exact patch used by the historical online audit.

The compressed alarm files store before/after warnings, edited paths, application status, and canonical diffs. Of the evaluated rows, 4,136 reuse earlier scan records, marked `scan_source=legacy`; agreement with fresh scans is recorded in `results/legacy_validation.csv`. Historical online alarm snapshots and the complete original patch archive are unavailable. These limitations prevent treating this export as a controlled reconstruction of all historical changes.

## Matchers
- **DA_MIXED:** in edited files, compare which rules have warnings before and after the patch; elsewhere, compare exact `(file, line, rule)` tuples. An additional warning of a rule already present in an edited file may therefore be missed.
- **EXACT:** compare raw `(file, line, rule)` sets everywhere. A warning merely shifted by an edit can be counted as new.
- **LOC-v1:** map baseline warning positions through the patch hunks before matching. Matching within rewritten hunks uses a one-to-one, same-rule absorption pool; a removed warning can absorb at most one post warning. Cross-hunk and cross-file moves are not generally matched.
- **LOC-v2:** the same procedure with exact repository-relative path lookup instead of the boundary-less suffix lookup used by v1. All observed v1/v2 verdicts agree.

`MATCHER_NOTES.md` records tested cases, path-matching behavior, and limitations, including project-level warnings at line zero. LOC is a finer comparator, not a semantic ground truth. `results/flip_review_ai.csv` is static AI triage, not human review; compile-error checks and configuration-dependent cases remain identified separately.

## Statistics
`results/sig_check_ohmas_vs_baselines.csv` contains Full versus each of 12 single-attempt baselines for both language layers, under DA and LOC. Its 95% CIs use 20,000 paired file-snapshot-cluster bootstrap resamples, with cluster-level sign-flip tests and seed 20261118. Clusters are reconstructed from project, commit, and target file.

`results/additional_paired_stats.csv` contains the overall Full-versus-Moatless-three-attempt and A1-versus-Moatless-three-attempt comparisons, and the two reversed C/C++ baseline pairs, under each matcher. It uses 10,000 paired instance bootstrap resamples with seed 20261110 and two-sided exact McNemar tests. These are distinct procedures and result scopes; they are not the primary-paper CIs or the clustered HapRepair analysis.

Ranking files compare controlled outcomes within the defined scope. A ranking reversal is distinct from a change involving a tie. Statistical findings describe the fixed offline patch collection and do not establish that online repair loops would produce the same patches under LOC.
