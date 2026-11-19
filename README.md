# OH-MAS: Contracting the Search Space for Static-Analysis Warning Repair

> Artifact for our ICSE 2027 submission. OH-MAS is an automated repair system for OpenHarmony that reaches **85.0% strict pass rate (SPR)** on 741 real-world static-analysis warnings (81.4% ArkTS, 88.9% C/C++), 28.5 pp ahead of the strongest single-attempt agent baseline (Moatless, 56.5%) and 19.3 pp ahead of the strongest baseline given three linter-fed attempts (GPT-5.5, 65.7%).

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
│   ├── src/oh_mas/          # agents (AO/GW/CP/DA), core, oh_kb, gw_semantic_lib, run
│   └── config/              # YAML configs: default, exp_p1/p2/p3 (RQ1/RQ3), rev_* (RQ2 ablations)
├── mini-swe-agent/          # Adapted mini-SWE-agent (CP worker backbone)
├── oh-kb/                   # Knowledge base (rule templates + repair_experiences.json + dependency graphs)
├── dataset/                 # OH-Bench (ArkTS + C/C++) and manual-review subsets
├── docker/Dockerfile        # Linter (CodeLinter / Cppcheck) image
├── Per-Instance Analysis Report/   # Per-instance result CSVs for all tables
└── OH_MAS_appendix.pdf      # Supplementary material
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
docker build -t harmonyrepair:with-codelinter docker/
```

---

## Dataset (OH-Bench)

The `dataset/` directory contains OH-Bench, a repository-level benchmark of **741 real-world warnings** from 39 industrial OpenHarmony repositories:


| File                              | Instances | Language | Purpose                                   |
| ----------------------------------- | ----------- | ---------- | ------------------------------------------- |
| `arkts_dataset_final.json`        | 382       | ArkTS    | Full benchmark (RQ1-RQ3)                  |
| `cpp_dataset_final.json`          | 359       | C/C++    | Full benchmark (RQ1-RQ3)                  |
| `arkts_manual_review_subset.json` | 50        | ArkTS    | Manual review / error analysis            |
| `cpp_manual_review_subset.json`   | 50        | C/C++    | Manual review / error analysis            |

Each instance in the main benchmark contains: `instance_id`, alarm metadata (file, line, rule, message), and the buggy repository at a fixed commit. All experiments (including ablations) run on the full 741 instances.

The manual review subsets are 50-instance random samples (`seed=42`) drawn from the full benchmark for qualitative error analysis. They include additional fields — `input_snippet`, `code_lines`, and `category` — to support case-by-case inspection of repair failures.

> **Note:** The 39 source repositories are not included due to size and third-party licensing. Each instance records the full 40-character commit hash; repositories can be cloned from OpenHarmony Gitee and checked out at the recorded commit. See `dataset/arkts_dataset_final.json` → `commit_hash` field.

---

## Configuration

Copy and edit the example config:

```bash
cp oh-mas-backend/config/exp_p1.yaml oh-mas-backend/config/my_run.yaml
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

Per-instance outcomes (result, failure level, PAR, Pass@1) behind every table are in `Per-Instance Analysis Report/`.

### RQ1 — Main effectiveness

OH-MAS (pool P1, `exp_p1.yaml`) on all 741 instances, against 12 baselines (SPR %):


| System                                 | ArkTS    | C/C++    | Overall  |
| ---------------------------------------- | ---------- | ---------- | ---------- |
| Best direct LLM, 1 att. (GPT-5.5)      | 56.0     | 55.2     | 55.6     |
| Best direct LLM, 3 att. (GPT-5.5)      | 72.8     | 58.2     | 65.7     |
| Best agent, 1 att. (Moatless)          | 58.4     | 54.6     | 56.5     |
| **OH-MAS (P1)**                        | **81.4** | **88.9** | **85.0** |
| Sonnet 4.5, 3 att.                     | 63.9     | 56.3     | 60.2     |
| Moatless, 3 att.                       | 74.1     | 67.1     | 70.7     |
| OH-MAS, Sonnet 4.5 only (same budget)  | 73.6     | 77.7     | 75.6     |

Full baseline rows (Opus 4.5/4.8, Sonnet 4.5, Gemini 2.5 Flash, Kimi K2.5, MiniMax M2.5, Agentless, SWE-agent, Claude Code, OpenCode) are in `arkts_baselines_per_instance.csv`, `cpp_baselines_per_instance.csv`, `baselines_3att_per_instance.csv`, and `gpt55_3att_per_instance.csv`.

### RQ2 — Ablation study

Run on all 741 instances; per-instance results in `ablation_per_instance.csv`:


| Variant                       | Config                | Overall SPR | Δ     |
| ------------------------------- | ----------------------- | ------------- | ------- |
| Full (OH-MAS, P1)             | `exp_p1.yaml`         | 85.0        | —     |
| A1 (single backbone)          | `rev_a1_sonnet.yaml`  | 75.6        | −9.4  |
| A2 (no typed feedback)        | `rev_a2.yaml`         | 57.6        | −27.4 |
| A2′ (free-form, non-cumul.)   | `rev_a2prime.yaml`    | 69.2        | −15.8 |
| A2″ (free-form, cumulative)   | `rev_a2pp.yaml`       | 70.2        | −14.8 |
| A3 (no repair contract)       | `rev_a3.yaml`         | 65.3        | −19.7 |
| A3′ (no knowledge base)       | `rev_a3prime.yaml`    | 76.0        | −9.0  |
| Templates only                | `rev_tpl_only.yaml`   | 74.2        | −10.8 |
| Experiences only              | `rev_exp_only.yaml`   | 81.8        | −3.2  |
| Random routing                | `rev_random.yaml`     | 81.0        | −4.0  |
| Three Sonnet workers          | `rev_sonnet3.yaml`    | 79.5        | −5.5  |

### RQ3 — Cost-effectiveness

Three CP model pools on all 741 instances (`exp_p1/p2/p3.yaml`; per-instance results in `main_P1-P3_per_instance.csv`):


| Pool            | ArkTS | C/C++ | Overall | Pass@1 | $/instance |
| ----------------- | ------- | ------- | --------- | -------- | ------------ |
| P1 (Balanced)   | 81.4  | 88.9  | 85.0    | 54.7   | 0.46       |
| P2 (Cost-Eff.)  | 79.8  | 82.2  | 81.0    | 43.3   | 0.26       |
| P3 (Frontier)   | 78.3  | 86.6  | 82.3    | 54.0   | 0.66       |

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

The `oh-kb/` directory is a unified knowledge base of rule-specialized repair templates and curated repair experiences used by GW (`allowed transformations`). Pre-built dependency graphs are in `Per-Instance Analysis Report/graph_explore/`. These are JSON files organized by linter category:

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

- `null` — no KB (ablation A3′)
- `graph_explore_mock` — uses pre-built dependency graphs + oh-kb templates (default)

The KB client implementation and dependency graph construction logic reside in `oh-mas-backend/src/oh_mas/oh_kb/`.

---

## Docker Image

The `docker/Dockerfile` builds the linter execution environment:

```bash
docker build -t harmonyrepair:with-codelinter docker/
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
