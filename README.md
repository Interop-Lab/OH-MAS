# OH-MAS: Contracting the Search Space for Static-Analysis Warning Repair

[![Stars](https://img.shields.io/github/stars/Interop-Lab/OH-MAS?style=social)](https://github.com/Interop-Lab/OH-MAS)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**OH-MAS** (OpenHarmony Multi-Agent System) is an automated repair system that fixes static-analysis warnings across OpenHarmony's heterogeneous codebase. It reaches a **77.9% strict pass rate** on 741 real-world warnings (382 ArkTS + 359 C/C++), outperforming the strongest agent baseline by **30.7 percentage points**, at $0.25–$0.66 per warning.

---

## What Makes OH-MAS Different?

Static-analysis tools flag millions of warnings in industrial codebases. Conventional LLM agents treat repair as free-form code generation: they edit whatever they want, react to failures with generic natural-language reflection, and handle every warning with the same model. On heterogeneous, framework-heavy code like OpenHarmony — where C/C++ system code and ArkTS application code coexist under different linters — this unbounded approach breaks down.

OH-MAS takes a different approach. It casts warning repair as a **bounded search**: each failed attempt is diagnosed with a precise type, and that diagnosis contracts the search space so the next attempt is smaller and better targeted. Three coupled mechanisms make this possible:

### 1. Repair Contracts — Bounding the Edit Space

The **Graph Weaver** slices the project dependency graph into a typed triple before any code is touched:

- **Must-fix** locations that need to change
- **Must-not-touch** invariants (public signatures, framework decorators like `@State`/`@Link`, cross-file types)
- **Allowed transformations** drawn from  an offline knowledge base

The Constrained Patcher may act only inside this contract, which turns free-form generation into constraint-satisfaction within a bounded space.

### 2. Typed Diagnostic Feedback — Contracting the Space

The **Diagnostic Auditor**  labels every failure with a concrete type:


| Level  | Meaning                                                 | Constraint                              |
| -------- | --------------------------------------------------------- | ----------------------------------------- |
| **L1** | Applicability — patch does not apply cleanly           | Fix the syntactic conflict              |
| **L2** | Target warning — the original warning is still present | The edit did not address the root cause |
| **L3** | Regression — a new defect was introduced               | Narrow what may be touched              |

These constraints are **never discarded**. From round t to round t+1, the admissible patch space is non-increasing: Ω_{t+1} ⊆ Ω_t. The same diagnostic that tightens the contract also widens the model pool.

### 3. Complementarity-aware Model Pool — Matching the Generator

The **Adaptive Orchestrator** profiles each warning by rule category, language layer (ArkTS or C/C++),  current mode, and failure tier (L1, L2, or L3), then routes it through a heterogeneous pool of models. As difficulty rises from easy to hard, more models join the search:

```
easy   → [Claude Sonnet 4.5]
medium → [Claude Sonnet 4.5, Gemini 2.5 Flash]
hard   → [Claude Sonnet 4.5, Gemini 2.5 Flash, Kimi K2]
```

No single model dominates across rule families and language layers; the pool discovers complementarity as a first-class signal.

### The Coupling That Makes It Work

These three mechanisms reinforce each other: the diagnostic that tightens the contract is the same signal that widens the pool, and the contract bounds what every pooled model may do. Each rejection leaves the next attempt both **smaller** (constrained space) and **better aimed** (better-matched model). Free-form retry becomes a search that contracts with every failure.

> **Comparison with existing systems:**
>
>
> | System        | Bounded space | Typed feedback | Multi-model | Target domain      |
> | --------------- | :-------------: | :--------------: | :-----------: | -------------------- |
> | SWE-agent     |      ✗      |       ✗       |     ✗     | Python (SWE-bench) |
> | Agentless     |    partial    |       ✗       |     ✗     | Python (SWE-bench) |
> | RepairAgent   |      ✗      |       ✗       |     ✗     | Java (Defects4J)   |
> | AutoCodeRover |    partial    |       ✗       |     ✗     | Python / Java      |
> | HapRepair     |      ✗      |       ✗       |     ✗     | ArkTS only         |
> | **OH-MAS**    |    **✓**    |     **✓**     |   **✓**   | **C/C++ + ArkTS**  |

---

## System Overview

[![OH-MAS System Overview](https://img.shields.io/badge/Overview-PDF-blue)](overview.pdf)

Overview of OH-MAS. The Adaptive Orchestrator (AO) profiles the warning and sets the execution mode. The Graph Weaver (GW) slices the dependency graph to extract a Repair Contract comprising must-fix locations, must-not-touch invariants, and allowed transformations. The Constrained Patcher (CP) generates patches within this contract using a heterogeneous model pool. The deterministic Diagnostic Auditor (DA) validates candidates across three hierarchical tiers: applicability (L1), target-warning (L2), and regression (L3). Failed audits return typed evidence, which the AO accumulates as monotonically tightening constraints for the next round.

---

## Repository Structure

```
OH-MAS/
├── oh-mas-backend/          # Core system (Python)
│   ├── src/oh_mas/
│   │   ├── agents/          # AO, GW, CP, DA agent implementations
│   │   ├── core/            # Orchestrator, event bus, workspace, schemas
│   │   ├── oh-kb/           # dependency graphs construction
│   │   └── run/             # CLI entry point
│   └── config/              # YAML configurations
├── mini-swe-agent/          # Adapted mini-SWE-agent (CP worker backbone)
├── oh-kb/                   # Unified knowledge base (repair templates + experiences)
├── data/                    # OH-Bench dataset (ArkTS + C/C++)
├── scripts/
│   ├── sample_ablation_subset.py   # Stratified sampling for ablation
│   └── sample_manual_review.py     # Random sampling for manual review
├── results/
│   └── analysis_reports/    # Evaluation reports
├── docker/
│   └── harmonyrepair-codelinter/Dockerfile
├── overview.pdf             # System architecture diagram
└── Appendix.pdf             # Supplementary material
```

---

## Quick Start

### Requirements

- Python 3.10+
- Docker (for linter execution)
- Git, ripgrep
- LLM API access (OpenRouter recommended)


| Component | Minimum | Recommended                    |
| ----------- | --------- | -------------------------------- |
| CPU       | 4 cores | 8+ cores (parallel workers)    |
| RAM       | 16 GB   | 32 GB                          |
| Disk      | 50 GB   | 100 GB (repositories + traces) |

### Installation

```bash
git clone https://github.com/Interop-Lab/OH-MAS.git && cd OH-MAS

# Create virtual environment
python3 -m venv venv && source venv/bin/activate

# Install dependencies
pip install -e mini-swe-agent/
pip install -e oh-mas-backend/

# Build Docker image for linter validation
cd docker/harmonyrepair-codelinter
docker build -t harmonyrepair:with-codelinter .
cd ../..
```

### Example: Repairing an ArkTS Warning

1. **Prepare a configuration file:**

```bash
cp oh-mas-backend/config/oh_mas.yaml oh-mas-backend/config/my_run.yaml
```

2. **Set your API key:**

```bash
export OPENROUTER_API_KEY=sk-...
```

3. **Run OH-MAS on a single warning:**

```bash
cd oh-mas-backend
PYTHONPATH=src python3 -m oh_mas.run.oh_mas run \
  --config config/my_run.yaml \
  --task-id ARKTS_0001 \
  --alarm-file entry/src/main/ets/pages/Index.ets \
  --alarm-rule hp-arkui-use-reusable-component \
  --alarm-message "LazyForEach child component should be @Reusable"
```

The system will profile the warning, emit a Repair Contract, and iterate through the model pool until the patch passes all three audit levels (L1/L2/L3) or the maximum retry budget is exhausted.

For key configuration options, see the default `oh-mas-backend/config/oh_mas.yaml`:

```yaml
runtime:
  execution_mode: docker_whole            # docker_whole | host

ao:
  llm_model: openrouter/google/gemini-2.5-flash

cp:
  max_parallel_workers: 3
  mode_strategy:
    easy:   [openrouter/anthropic/claude-sonnet-4.5]
    medium: [openrouter/anthropic/claude-sonnet-4.5, openrouter/google/gemini-2.5-flash]
    hard:   [openrouter/anthropic/claude-sonnet-4.5, openrouter/google/gemini-2.5-flash,
             openrouter/moonshot-ai/kimi-k2]
```

---

## Dataset (OH-Bench)

OH-Bench is a repository-level benchmark of **741 real-world static-analysis warnings** from 39 industrial OpenHarmony repositories, spanning two language layers:


| File                              | Instances | Language | Purpose        |
| ----------------------------------- | ----------- | ---------- | ---------------- |
| `arkts_dataset_final.json`        | 382       | ArkTS    | Full benchmark |
| `cpp_dataset_final.json`          | 359       | C/C++    | Full benchmark |
| `arkts_ablation_subset.json`      | 100       | ArkTS    | Ablation study |
| `cpp_ablation_subset.json`        | 100       | C/C++    | Ablation study |
| `arkts_manual_review_subset.json` | 50        | ArkTS    | Manual review  |
| `cpp_manual_review_subset.json`   | 50        | C/C++    | Manual review  |

Each instance includes: `instance_id`, alarm metadata (file, line, rule, message), the buggy repository at a fixed commit, and the reference patch. The subsets are fully reproducible (seed=42):

```bash
python scripts/sample_ablation_subset.py
python scripts/sample_manual_review.py
```

> The 39 source repositories are not bundled due to size and third-party licensing. Each instance records the full commit hash; repositories can be cloned from [OpenHarmony Gitee](https://gitee.com/openharmony) and checked out at the recorded commit.

## Knowledge Base (OH-KB)

The `oh-kb/` directory contains pre-built dependency graphs and rule-specialized repair templates used by the Graph Weaver. Templates are organized by linter category:

```
oh-kb/
├── repair_experiences.json     # Curated L3 repair experiences
├── @hw-stylistic/              # HarmonyOS style rules
├── @performance/               # Performance rules
├── cppcheck-logic/             # C++ logic errors
├── cppcheck-safety/            # Memory safety
└── ...
```

Each template file contains `rule_id`, buggy/fixed code examples, and explanations.The KB client implementation and dependency graph construction logic reside in `oh-mas-backend/src/oh_mas/oh_kb/` (a subpackage of `oh_mas`).

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

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=Interop-Lab/OH-MAS&type=Date)](https://star-history.com/#Interop-Lab/OH-MAS&Date)

---

## License

Source code: MIT. Dataset (OH-Bench): CC BY 4.0. Mini-swe-agent components retain their original Apache-2.0 license.
