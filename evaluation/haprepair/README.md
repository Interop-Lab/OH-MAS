# HapRepair Comparison

This directory contains task-level outcomes, reproduction configurations, rule-registry settings, and file-level warning counts for comparing OH-MAS and HapRepair. Statistical recomputation requires Python 3 and NumPy; it does not call a model or run a linter.

## Data and provenance

- `paired_per_instance_ohbench382.csv`: 382 paired tasks. `ohmas_p1` is joined by `instance_id` directly from `P1_result` in the current repository report `../../results/main_P1-P3_per_instance.csv` (ArkTS rows). This is source A. Its source CSV SHA-256 values and exact field mapping are in `label_source.json`. Matching-sensitivity recorded fields are synchronized to the same source; controlled offline matcher verdicts are separate.
- `hap_original`: outcomes from the public implementation with its original registry. `hap_extended`: new outcomes for the 44 unsupported tasks and retained original outcomes for the other 338 tasks. `is_new44` identifies the rerun group. The other 338 outcomes are unchanged.
- `paired_per_instance_external100.csv`: 100 paired external tasks. `ohmas_external` and `hap_paper_protocol` contain the task-level outcomes. Prefixes `OH_EXT_N` and `OH_EXT_T` identify the two 50-task groups without and with OH-MAS documentation templates, respectively.
- `file_snapshot_id`: project, commit, and target file, used as the resampling cluster.
- `external_file_alarm_counts.csv`: registered-rule warning counts before and after repair in 79 external files; the totals are 374 and 5. This is the defect-level measure, distinct from task-level SPR.
- `config/`: frozen model, retrieval, round-limit, audit, and environment settings. Sonnet 4.5 is used for repair/merging and GPT-4o for functional checks. File-level repair allows at most five outer rounds and three internal attempts per round.
- `registry/`: the nine added rule registrations, frozen hashes, task list, and runner changes. No OH-MAS experiences, templates, or task-specific repair guidance were added. Placeholder pages satisfy the original loader's file-existence requirement without supplying examples.

## Task-Level Results

The following statistics are computed from the paired task outcomes using the procedure below. The OH-MAS P1 outcomes use the frozen source identified in `label_source.json`.

| Comparison | OH-MAS passes | HapRepair passes | Difference (pp) | Recomputed 95% CI (pp) | Recomputed p |
|---|---:|---:|---:|---|---|
| OH-Bench, original registry | 311/382 | 266/382 | +11.8 | [4.2, 19.7] | 0.0029 |
| OH-Bench, extended registry | 311/382 | 276/382 | +9.2 | [2.1, 16.7] | 0.0152 |
| External tasks | 89/100 | 69/100 | +20.0 | [9.7, 30.5] | 0.0007 |

On the 44 rerun tasks, OH-MAS passes 43 and extended HapRepair passes 10. On the 338 originally covered tasks, OH-MAS passes 268 and HapRepair passes 266.

## Recompute

From this directory:

```bash
python3 scripts/paired_stats.py
```

The script uses 20,000 file-snapshot cluster bootstrap samples for percentile 95% CIs and a two-sided cluster sign-flip test with 20,000 draws, using seed 20261117. It writes its actual numerical output to `results/paired_stats.json`.

`results/paper_reference_results.json` preserves the values reported in the accompanying research document and is separate from this script's numerical output. Pass counts and significance conclusions agree, while some CI endpoints and p-values differ. Consult the two files for the exact reported and recomputed values.

## Evaluation scope

The extension reruns only 44 tasks. Historical later-round records do not establish whether adding registrations could affect later rounds of the retained 338 tasks; those outcomes are reused as recorded. Three previewer tasks produce no patch because their target warnings are absent from the default scan. They remain non-passing in the fixed denominator. The original-status column distinguishes unregistered rules, no-patch outcomes, tool errors, and DA failures.

Per-call API logs and complete original patch archives are not included. Home-directory prefixes are anonymized. Evaluation adaptations, selection conventions, and outcome limitations are retained because they define the comparisons.
