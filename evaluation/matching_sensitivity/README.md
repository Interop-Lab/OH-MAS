# Warning-Matching Sensitivity

This study re-scores **stored patches**: patch, code snapshot, apply step and scan are fixed; only the
rule that decides whether a post-patch alarm is *new* (L3) is switched. No model was called; the repair loops were **not** re-run with a location-aware DA.

## Two kinds of numbers - do not mix them
- `paper_recorded_*` columns (per_instance_verdicts.csv): primary outcomes joined by instance ID from the current repository per-instance reports. P1, P2, and P3 use source A, `../../results/main_P1-P3_per_instance.csv`. Native baselines, ablations, matched-budget baselines, and external OH-MAS use the corresponding reports from the same repository archive. `primary_record_sources.json` records the exact per-system mapping and CSV hashes. HapRepair-specific protocols retain their supplied labels. These recorded columns are never merged into controlled matcher verdicts. The HapRepair P1 pairing is joined directly from source A (`../haprepair/label_source.json`), and both copies agree on all 382 tasks. Three-attempt Moatless agrees with `../moatless_three_attempts/`: 283/382 ArkTS, 241/359 C/C++, 524/741 overall.
- `verdict_*` columns: the **controlled offline DA** of this study (scan with cppcheck 2.17.1 / codelinter 6.0.240 in image `harmonyrepair:with-codelinter`,
  `f825fefc...`; apply with `git apply --ignore-space-change --ignore-whitespace`). It is not the paper's online DA (e.g. P1: offline DA 648 pass vs recorded 630).
  Comparison 1 (EXACT / DA / LOC, same scan) and comparison 2 (recorded vs offline DA, `cmp2_*` in results/per_system_summary.csv) are reported separately.

`P1latest` is a retained system identifier. Its primary-record labels now come from source A. Its row in `results/per_system_summary.csv` aggregates both language datasets (741 tasks); per-language primary counts are in `../primary_statistics/`. Primary-source alignment does not replace the selected patches or their historical run statuses.

Matchers: `DA_MIXED` (current DA: rule-granular in patched files, exact tuples elsewhere), `EXACT` (raw (file,line,rule) set difference),
`LOC-v1` (hunk-mapped, one-to-one, default), `LOC-v2` (= v1 with exact path matching). v1 and v2 are identical on every task.

## Files
| file | content |
|---|---|
| `per_instance_verdicts.csv` | 24,035 rows (system, task): 4 verdicts, stage, `coverage_status` (EVALUATED / NOT_EVALUATED), `verdict_basis` (scanned / no_patch_counted_as_fail / not_evaluated), `not_evaluated_reason`, `scan_source` (fresh / legacy / none), patch status, source, sha256, project/commit/target_file/rule_id; `file_snapshot_id` is not populated for every row, so file-snapshot clusters are reconstructed from project, commit, and target file, `paper_recorded_*` |
| `patch_manifest.csv`, `patch_selection_rules.md` | patch origin, raw/effective sha256, selection rule (no patch texts) |
| `mapping.json`, `primary_record_sources.json` | system and primary-source mappings: label, dataset, task count, scopes (single12, tableIII, extra, ohbench_arkts, external100, ...) |
| `alarms/baseline_alarms.jsonl.gz`, `alarms/post_alarms.jsonl.gz` | structured alarms before (338 project@commit@linter baselines) and after (14,927 distinct patches; with `instances`, apply status, `patched_files`, `canonical_diff`): `[file, line, rule, message]`; message is null for `scan_source=legacy` records |
| `METHODS.md`, `MATCHER_NOTES.md` | matcher definitions, fixed evaluation procedure, statistical methods, and matching limitations |
| `results/` | `per_system_summary.csv`, `ranking_changes.csv`, `summary_stats.json`, `flip_review_ai.csv` (static triage of the 78 DA-pass/LOC-fail rows; not a human review), `legacy_validation.csv`, `syntaxError_cases.csv` (10 cases: gcc evidence + rescans), `rank_robustness.*`, `sig_check*`, `recompute_verdicts_report.json`, `verify_claims_report.txt` |

## Methods and recorded validation
This directory contains the per-instance verdicts, patch provenance, before/after alarm lists, statistical results, and method descriptions.
See `METHODS.md` for the controlled comparison and statistical procedures, and `MATCHER_NOTES.md` for matcher behavior and limitations.
`results/recompute_verdicts_report.json` and `results/verify_claims_report.txt` record validation performed when these results were prepared; analysis implementations are not included in this directory.

## Headline numbers (33/33 recorded checks passed)
12 single C++ systems: 739 tasks accepted by DA but rejected by EXACT, 0 reverse; 11 by LOC, 0 reverse. Max LOC flips per system: 3 (single12 C++), 5 (Table III C++, E1),
7 (extra C++, A1-Gemini), 1 (single12 ArkTS), 3 (32 OH-Bench ArkTS systems), 2 (external, OH-MAS). Strict ranking reversals under LOC: 2 (C++ single12 and Table III), 0 (ArkTS single12 / Table III), 1 (32 ArkTS systems); none involves OH-MAS vs a baseline,
all occur between DA-indistinguishable systems (p >= 0.84), Kendall tau 0.89-0.99. OH-MAS-vs-baseline significance (24 pairs) unchanged.

## Known limitations
- Post-hoc re-scoring, not a re-run of the repair loop with a location-aware DA; LOC is a stricter matcher, not ground truth. 78 DA-pass/LOC-fail rows exist (`flip_review_ai.csv`): 10 are cppcheck `syntaxError` (9 confirmed as gcc compile errors, 1 configuration-dependent; the current DA's case-sensitive syntax markers do not match cppcheck's lowercase `syntaxError`); the rest are triaged statically only.
- Failed-run patch choice is a convention (see `patch_selection_rules.md`); baselines keep earlier normalised patches; 4,136 evaluated rows reuse earlier scan records validated against fresh scans (`legacy_validation.csv`, 0 differences), marked `scan_source=legacy`.
- 584 no-patch tasks are counted as fail; 7 external OH-MAS tasks are not evaluated.

## Missing (not available, not reconstructed)
- Alarm snapshots of the **original online DA runs** (baseline/post warnings were never stored); hence no paper-aligned re-scoring.
- The text the original online audit applied for Agent C++ baselines (stage-2 canonical diffs), and raw scan stdout/stderr (kept in the authors' scan cache, not exported).
- The complete original patch archive. Canonical diffs needed for the controlled matcher recomputation are included in `alarms/post_alarms.jsonl.gz`; these do not replace a complete archive of raw generated and online-applied patches.
- Original scripts of the paper's clustered HapRepair comparison. The HapRepair directory supplies a documented recomputation and separately identifies the values reported in the paper.

## Overall matched-budget comparisons

`results/additional_paired_stats.csv` covers Full versus three-attempt Moatless, the same-model OH-MAS versus three-attempt Moatless, and the two reversed C/C++ baseline pairs, under both DA and LOC. These are controlled offline outcomes. CIs use 10,000 paired instance bootstrap samples (seed 20261110); p-values use the two-sided exact McNemar test. They are separate from the 24 per-language cluster-based comparisons in `results/sig_check_ohmas_vs_baselines.csv`.
