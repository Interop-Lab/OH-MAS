# Primary-Run Paired Statistics

The source of final P1/P2/P3 outcomes is `../../results/main_P1-P3_per_instance.csv` (source A). Other systems and subset membership use the corresponding current repository reports. `source_manifest.json` lists exact source CSV hashes.

- `paired_statistics.csv`: paired primary-run differences, percentile bootstrap 95% CIs and exact McNemar p-values, by comparison and subset. All included systems have the same 741 task IDs before subset selection.
- `subgroup_counts.csv`: overall, language, ArkUI-rule, and experience-rule counts and rates. The 399 experience-rule tasks span 11 target rules present in the benchmark; the library itself has 12 rule keys, one of which is not a benchmark target rule.
- `audit_membership_check.json`: the fixed 50+50 sample has 88 accepted tasks under source A (43 ArkTS, 45 C/C++), exactly matching the supplied 88 audit rows. No annotator labels were changed or invented.
- `method.json`: statistical procedures and direction. Comparisons use 10,000 paired bootstrap samples, sorted task IDs, seed 20261110 reset for each pair, and two-sided exact McNemar tests without multiple-comparison adjustment.

These statistics use primary task outcomes and do not use offline DA/EXACT/LOC verdicts. The controlled offline matching study remains in `../matching_sensitivity/`. HapRepair's paired analysis uses file-snapshot clusters and remains in `../haprepair/`.
Per-instance PAR and failure-level columns are omitted from the distributed reports; this does not change the supplied repair outcomes or paired SPR statistics. The source main CSV has no per-instance first-round or API-cost fields; these statistics do not independently reconstruct those quantities.
