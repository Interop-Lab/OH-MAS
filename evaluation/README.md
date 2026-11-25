# Evaluation Data and Methods

This directory contains task-level comparison studies, paired statistical results, evaluation methods, and environment verification records. Primary repair outcomes are in [`../results/`](../results/README.md).

| Directory | Contents |
|---|---|
| `docker_pinning/` | Original server delivery of the digest-pinned Docker build, lock files, checksums, and successful build evidence; active instructions are in [`docker/README.md`](../docker/README.md). |
| `primary_statistics/` | Primary-run paired statistics, subset counts, audit membership validation, methods, and current repository source hashes. |
| `haprepair/` | Original-registry and extended-registry HapRepair task outcomes, external results, configuration/method notes, and paired statistics. |
| `matching_sensitivity/` | Controlled offline EXACT/DA/LOC verdicts, patch provenance, alarm lists, statistical results, recorded validation, and method descriptions. Analysis implementations are not included in this directory. |
| `moatless_three_attempts/` | Three-attempt Moatless task outcomes, the nine C/C++ record updates, source fingerprints, and validation. |

Each subdirectory has its own README describing the result fields, scope, and methods.
For the controlled matching study, use `matching_sensitivity/METHODS.md` and `MATCHER_NOTES.md`.
Its offline matcher verdicts and recorded primary-run outcomes are separate fields and are not interchangeable.
P1/P2/P3 outcomes come from `../results/main_P1-P3_per_instance.csv`. The provenance records refer to this frozen source as source A. Primary-source hashes are in `primary_statistics/source_manifest.json`. HapRepair pairing and the matching-study primary-record fields are synchronized to that source. Their mappings are in `haprepair/label_source.json` and `matching_sensitivity/primary_record_sources.json`.

The compressed alarm files in `matching_sensitivity/alarms/` are data, not runtime logs or analysis programs.
The larger compressed file exceeds GitHub's ordinary Git single-file limit; distribute it using Git LFS or a release asset and preserve its path or document the download location.
