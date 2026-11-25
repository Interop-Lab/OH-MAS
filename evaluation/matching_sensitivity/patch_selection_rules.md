# Which patch was evaluated for each (system, task)

`patch_manifest.csv` lists, for every (system, task), the patch that was frozen and evaluated (sha256 only; patch texts are not in this export).
Column `patch_store_source` / `selection_reason` / `provenance_status` give the origin; the rules are:

1. **Trace-based systems** (P1 = `P1latest`, P2/P3, ablations E1-E10, A1-Gemini/Kimi):
   - the revision_results patch store (`main_patches/`, `ablation_patches/`, `a1alt_patches/`) is authoritative;
   - a run that finished DONE uses the accepted patch (store text = accepted candidate of that run);
   - a failed run (STOP_FAILED) in the ablation/A1 stores, or any failed run whose store entry is empty, uses the **first non-empty candidate of the last
     `patches.ready` event** of the organised trace (`patch_store_source = last_round_first_candidate(trace)`), as the selection convention for this offline study. This is a
     convention, not a record of what the run "chose" (a failed run designates no patch). It only matters for failed tasks that reach L3.
   - P1 ArkTS OH_0023/0060/0082/0101/0269/0271/0327/0359 keep the patch from the original-run logs (as in the store).
2. **Baselines** (12 single-attempt systems, three-attempt x3 systems), **HapRepair** and the **external 100**: the earlier normalised patches were kept
   (not re-derived from the raw outputs in the store); for external OH-MAS the accepted patch of the recorded run trace.
3. **No patch** (empty patch, or nothing recoverable): `patch_status` is KNOWN_NO_PATCH / MISSING_ARTIFACT; verdict `NO_PATCH`, **counted as fail under every matcher and kept in the denominator**.
4. **Not evaluated**: external OH-MAS N009, N050, T009, T010, T026, T028, T030 (failed run, 2-3 tied last-round candidates, no store entry). Listed as `NOT_EVALUATED`.
