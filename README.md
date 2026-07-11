# OH-MAS: Contracting the Search Space for Static-Analysis Warning Repair

> Artifact for our ICSE 2027 submission. OH-MAS is an automated repair system for OpenHarmony that reaches **77.9% strict pass rate** on 741 real-world static-analysis warnings, outperforming the strongest agent baseline by 30.7 pp.

---

## Overview

Static-analysis linters generate millions of warnings in industrial codebases. Conventional LLM agents leave three decisions implicit: the edit space they explore, what they learn from each rejected attempt, and which model proposes the next fix. OH-MAS (OpenHarmony Multi-Agent System) makes all three explicit:

1. **Repair Contracts** (Graph Weaver) — bound the edit space with must-fix locations, must-not-touch invariants, and rule-specific transformation templates sliced from the project dependency graph.
2. **Typed Diagnostic Feedback** (Diagnostic Auditor) — label every failure as L1 (applicability), L2 (target warning), or L3 (regression), and accumulate them as monotonically tightening constraints across rounds.
3. **Complementarity-aware model pool** (Adaptive Orchestrator + Constrained Patcher) — route each round to a subset of a heterogeneous model pool and widen it as difficulty rises.

The system targets OpenHarmony's two language layers: **ArkTS** (validated by CodeLinter) and **C/C++** (validated by Cppcheck).

---

## Repository Structure

```
OH-MAS/
├── oh-mas-backend/          # Core system (Python)
│   ├── src/oh_mas/
│   │   ├── agents/          # AO, GW, CP, DA agent implementations
│   │   ├── core/            # Orchestrator, event bus, workspace, schemas
│   │   ├── oh-kb/           # KB client & graph construction
│   │   └── run/             # CLI entry point
│   └── config/              # YAML configurations (one per experiment)
├── mini-swe-agent/          # Adapted mini-SWE-agent (CP worker backbone)
├── oh-kb/                   # Unified knowledge base (repair templates + experiences)
├── data/                    # OH-Bench dataset (ArkTS + C/C++)
├── scripts/
│   ├── sample_ablation_subset.py   # Reproducible stratified sampling for RQ2
│   └── sample_manual_review.py     # Random sampling for manual review
├── results/
│   └── analysis_reports/    # Evaluation result reports
└── docker/
    └── harmonyrepair-codelinter/Dockerfile
```

---

## Requirements

- Python 3.10+
- Docker (for linter execution; CodeLinter requires the pre-built image)
- Git, ripgrep
- LLM API access (OpenRouter recommended; see Configuration)

### Hardware


| Component | Minimum | Recommended                    |
| ----------- | --------- | -------------------------------- |
| CPU       | 4 cores | 8+ cores (parallel workers)    |
| RAM       | 16 GB   | 32 GB                          |
| Disk      | 50 GB   | 100 GB (repositories + traces) |

---

## Installation

```bash
# 1. Clone the repository
git clone <REPO_URL> OH-MAS && cd OH-MAS

# 2. Create a virtual environment
python3 -m venv vene
source vene/bin/activate

# 3. Install oh-mas-backend and its mini-swe-agent dependency
pip install -e mini-swe-agent/
pip install -e oh-mas-backend/

# 4. Build the Docker image (needed for linter-based validation)
cd docker/harmonyrepair-codelinter
docker build -t harmonyrepair:with-codelinter .
cd ../..
```

---

## Dataset (OH-Bench)

The `data/` directory contains OH-Bench, a repository-level benchmark of **741 real-world warnings** from 39 industrial OpenHarmony repositories:


| File                              | Instances | Language | Purpose                        |
| ----------------------------------- | ----------- | ---------- | -------------------------------- |
| `arkts_dataset_final.json`        | 382       | ArkTS    | Full benchmark (RQ1, RQ3)      |
| `cpp_dataset_final.json`          | 359       | C/C++    | Full benchmark (RQ1, RQ3)      |
| `arkts_ablation_subset.json`      | 100       | ArkTS    | Stratified subset (RQ2)        |
| `cpp_ablation_subset.json`        | 100       | C/C++    | Stratified subset (RQ2)        |
| `arkts_manual_review_subset.json` | 50        | ArkTS    | Manual review / error analysis |
| `cpp_manual_review_subset.json`   | 50        | C/C++    | Manual review / error analysis |

Each instance in the main benchmark contains: `instance_id`, alarm metadata (file, line, rule, message), the buggy repository at a fixed commit, and the reference patch.

The manual review subsets (`*_manual_review_subset.json`) are 50-instance samples drawn from the full benchmark for qualitative error analysis. They include additional fields — `input_snippet`, `code_lines`, and `category` — to support case-by-case inspection of repair failures.

All subsets are fully reproducible from the full benchmark using the provided scripts (both use `seed=42`):

```bash
# Regenerate ablation subset (100 ArkTS + 100 C/C++, stratified by rule_id)
python scripts/sample_ablation_subset.py

# Regenerate manual review subset (50 ArkTS + 50 C/C++, simple random sample)
python scripts/sample_manual_review.py
```

> **Note:** The 39 source repositories are not included due to size and third-party licensing. Each instance records the full 40-character commit hash; repositories can be cloned from OpenHarmony Gitee and checked out at the recorded commit. See `data/arkts_dataset_final.json` → `commit_hash` field.

---

## Configuration

Copy and edit the example config:

```bash
cp oh-mas-backend/config/oh_mas.yaml oh-mas-backend/config/my_run.yaml
```

Key settings:

```yaml
runtime:
  repositories_root: ../../repositories   # cloned OH repos
  execution_mode: docker_whole            # docker_whole | host

oh_kb:
  provider: graph_explore_mock            # null | local_seed | graph_explore_mock

ao:
  llm_model: openrouter/google/gemini-2.5-flash

gw:
  llm_model: openrouter/anthropic/claude-sonnet-4.5

cp:
  max_parallel_workers: 3
  mode_strategy:
    easy:   [openrouter/anthropic/claude-sonnet-4.5]
    medium: [openrouter/anthropic/claude-sonnet-4.5, openrouter/google/gemini-2.5-flash]
    hard:   [openrouter/anthropic/claude-sonnet-4.5, openrouter/google/gemini-2.5-flash,
             openrouter/moonshot-ai/kimi-k2]
```

Set your API key:

```bash
export OPENROUTER_API_KEY=sk-...
# or
export ANTHROPIC_API_KEY=sk-...
```

---

## Running OH-MAS

### Single instance

```bash
cd oh-mas-backend
PYTHONPATH=src python3 -m oh_mas.run.oh_mas run \
  --config config/my_run.yaml \
  --task-id ARKTS_0001 \
  --alarm-file entry/src/main/ets/pages/Index.ets \
  --alarm-rule hp-arkui-use-reusable-component \
  --alarm-message "LazyForEach child component should be @Reusable"
```

---

## Reproducing Paper Results

### RQ1 — Main effectiveness (Table 2)

Run `oh_mas.yaml` (P1 pool: Claude Sonnet 4.5, Gemini 2.5 Flash, Kimi K2.5) on the full 741-instance benchmark. Pre-computed results are in `results/analysis_reports/`:


| File                   | Content                       |
| ------------------------ | ------------------------------- |
| `p1__arkts_report.txt` | ArkTS full benchmark, pool P1 |
| `p1__cpp_report.txt`   | C/C++ full benchmark, pool P1 |
| `p2__arkts_report.txt` | Pool P2 (cost-effective)      |
| `p3__arkts_report.txt` | Pool P3 (frontier)            |

### RQ2 — Ablation study (Table 3)

Run on the 200-instance stratified subset (`arkts_ablation_subset.json` + `cpp_ablation_subset.json`):


| Variant                  | Config file                                                             | Report                                            |
| -------------------------- | ------------------------------------------------------------------------- | --------------------------------------------------- |
| Full (OH-MAS)            | `oh_mas.yaml`                                                           | `eval_results_p1__arkts_report.txt`               |
| A1 (no model pool)       | `ablation1_claude.yaml`、ablation_a1_gemini.yaml、ablation_a1_kimi.yaml | `eval_results_ablation1_claude__arkts_report.txt` |
| A2 (no typed feedback)   | `ablation2.yaml`                                                        | `eval_results_ablation2__arkts_report.txt`        |
| A2' (free-form feedback) | `ablation2prime.yaml`                                                   | `eval_results_ablation2prime__arkts_report.txt`   |
| A3 (no repair contract)  | `ablation3.yaml`                                                        | `eval_results_ablation3__arkts_report.txt`        |
| A3' (no templates)       | `ablation3prime.yaml`                                                   | `eval_results_ablation3prime__arkts_report.txt`   |

### RQ3 — Cost-effectiveness (Table 4)

Pre-computed in `results/analysis_reports/p2__*_report.txt` and `p3__*_report.txt`.

---

## Architecture

```
Alarm Input
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  AO (Adaptive Orchestrator)                          │
│  · Profiles alarm (language, rule category)          │
│  · Selects mode: easy → medium → hard               │
│  · Accumulates typed constraints across rounds       │
└──────────────────────┬──────────────────────────────┘
                       │ TaskProfiledEvent
                       ▼
┌─────────────────────────────────────────────────────┐
│  GW (Graph Weaver)                                   │
│  · Slices project dependency graph                   │
│  · Emits Repair Contract: must-fix / must-not-touch  │
│    / allowed transformations                         │
└──────────────────────┬──────────────────────────────┘
                       │ ContextReadyEvent
                       ▼
┌─────────────────────────────────────────────────────┐
│  CP (Constrained Patcher)                            │
│  · Queries heterogeneous model pool in parallel      │
│  · Each worker runs a mini-swe-agent loop            │
│  · Constrained by the Repair Contract                │
└──────────────────────┬──────────────────────────────┘
                       │ PatchesReadyEvent
                       ▼
┌─────────────────────────────────────────────────────┐
│  DA (Diagnostic Auditor)  [deterministic]            │
│  · L1: patch applies cleanly?                        │
│  · L2: target warning eliminated?                    │
│  · L3: no regressions introduced?                    │
└──────────────────────┬──────────────────────────────┘
                       │ AuditDoneEvent
                       ▼
              Pass → Done   |   Fail → retry (max 2)
                                  ↑ typed constraint appended
```

Each failed audit returns a **typed** verdict (L1/L2/L3) that the AO converts into a hard constraint and permanently appends to the prompt, so the admissible patch space `Ω` is non-increasing across rounds: `Ω_{t+1} ⊆ Ω_t`.

---

## Knowledge Base (OH-KB)

The `oh-kb/` directory is a unified knowledge base containing pre-built dependency graphs and rule-specialized repair templates used by GW (`allowed transformations`). These are JSON files organized by linter category:

```
oh-kb/
├── repair_experiences.json     # Curated L3 repair experiences
├── @hw-stylistic/              # HarmonyOS style rules
├── @performance/               # Performance rules
├── cppcheck-logic/             # C++ logic errors
├── cppcheck-safety/            # Memory safety
└── ...
```

Each template file contains `rule_id`, `examples` (buggy/fixed code pairs), and `explanation`.

The OH-KB supports multiple providers (set in config):

- `null` — no KB, for ablation A3'
- `graph_explore_mock` — uses pre-built dependency graphs + oh-kb templates (default)

The KB client implementation and dependency graph construction logic reside in `oh-mas-backend/src/oh_mas/oh_kb/` (a subpackage of `oh_mas`).

---

## Docker Image

The `docker/harmonyrepair-codelinter/Dockerfile` builds the linter execution environment:

```bash
docker build -t harmonyrepair:with-codelinter docker/harmonyrepair-codelinter/
```

This image contains CodeLinter (OpenHarmony's official ArkTS static analyzer) and Cppcheck. The DA agent mounts each repository into `/workspace/repo` and runs linters in isolation.

---

## Citing

```bibtex
@inproceedings{ohmas2027icse,
  title     = {{OH-MAS}: Contracting the Search Space for Static-Analysis Warning Repair},
  author    = {Anonymous},
  booktitle = {Proceedings of ICSE 2027},
  year      = {2027},
}
```

---

## License

Source code: MIT. Dataset (OH-Bench): CC BY 4.0. Mini-swe-agent components retain their original Apache-2.0 license.
