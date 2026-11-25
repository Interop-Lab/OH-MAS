# Per-Instance Repair Results

The CSV files in this directory record OH-Bench repair outcomes and the oracle-verdict review. Task identifiers join these results to the benchmark definitions in `../dataset/`.

| File | Contents |
|---|---|
| `main_P1-P3_per_instance.csv` | Outcomes for the balanced P1, cost-effective P2, and frontier P3 model pools. |
| `arkts_baselines_per_instance.csv` | Single-attempt model and agent outcomes for 382 ArkTS tasks. |
| `cpp_baselines_per_instance.csv` | Single-attempt model and agent outcomes for 359 C/C++ tasks. |
| `baselines_3att_per_instance.csv` | Direct-model and Moatless outcomes with up to three linter-fed attempts. |
| `gpt55_3att_per_instance.csv` | GPT-5.5 outcomes with up to three linter-fed attempts. |
| `ablation_per_instance.csv` | Component ablations and worker/routing controls on 741 tasks. |
| `single_model_gemini_kimi_per_instance.csv` | Single-model Gemini and Kimi configurations. |
| `external_ohbench_per_instance.csv` | OH-MAS outcomes on the 100 external ArkTS tasks. |
| `experience_instances.csv` | Rule membership and curation-audit membership for experience-related comparisons. |
| `oracle_audit_per_instance.csv` | Independent annotator labels and consensus for 88 DA-accepted patches from the fixed 100-task review sample. |

`PASS` and `FAIL` denote the recorded repair outcomes under the evaluation protocol. These are separate from controlled offline DA/EXACT/LOC verdicts in [`../evaluation/matching_sensitivity/`](../evaluation/matching_sensitivity/README.md).

Paired comparisons, statistical methods, and source fingerprints are in [`../evaluation/primary_statistics/`](../evaluation/primary_statistics/README.md). HapRepair uses the same ArkTS P1 outcome vector, with its pairing described in [`../evaluation/haprepair/`](../evaluation/haprepair/README.md).

Per-instance PAR and failure-level columns are not distributed. First-round fields are supplied only where available; the P1/P2/P3 main CSV has no per-instance first-round or API-cost records. These files alone therefore do not reconstruct every aggregate PAR, Pass@1, or cost value.
