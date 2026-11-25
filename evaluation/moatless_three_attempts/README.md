# Moatless with Three Linter-Fed Attempts

Recorded task outcomes: C++ 241/359 (67.1%), ArkTS 283/382 (74.1%), overall 524/741 (70.7%). All three are supported per instance (verified 2026-11-21).

## Outcome Provenance
- **241 (final):** `revision_results/baselines_3att_per_instance.csv`, column `Moatless_3att_result` (file dated 2026-11-19). It agrees task by task with each task's `revision_results/x3_traces/Moatless_x3/<id>/selected.json` (`passed`, `level`, `source`): 359/359 C++ and 382/382 ArkTS match.
- **232 (stale):** the earlier `paper_recorded_pass` export for `cpp_moatless3`. That export was built from the earlier table `revision_results/baselines_x3_per_instance.csv`, column `Moatless_x3_result` (read by the controlled-rescoring build script). That file no longer exists under that name (renamed, columns renamed to `*_3att_*`), so its original bytes cannot be re-hashed; the 232 survives only in the rescoring inputs (`systems2/cpp_moatless3.csv`, `recorded_pass`).

## The 9 differing tasks (`diff_232_vs_241.csv`)
CPP_0001, 0032, 0036, 0115, 0116, 0117, 0316, 0317, 0331: all FAIL (L1) in 232, all PASS in 241. All 9 are old-FAIL to new-PASS; no task moved the other way. In the traces: attempt 1 was originally audited `failed L1`; `rejudge_a1.json` (written 2026-11-17 17:27) re-judged it with the whitespace-tolerant apply (`fixed_by: ws`, `result: passed`), and `selected.json` records `source: rejudged`, `selected_attempt: 1`. The other 350 C++ tasks have `source: orig`. So 241 is supported per instance, not a bare total plus nine.

## Files
- `cpp_per_instance.csv`: 359 C++ rows. `per_instance.csv`: C++ and ArkTS (741 rows). Fields: `result` PASS/FAIL, `pass_at_1`, trace selection fields, `result_source_file/column`, `trace_file`.
- `diff_232_vs_241.csv`: the 9 tasks with the rejudge evidence.
- `source_sha256.csv`: SHA-256 of the CSV and of every `selected.json` / `rejudge_a1.json` used (paths relative to `OH-MAS/`).

## Consistency with the controlled rescoring
The controlled DA/EXACT/LOC verdicts were not changed. Their `paper_recorded_pass` columns for `cpp_moatless3` and `moatless3` have been synchronized by task ID with the final 359-row and 741-row exports. The C/C++ recorded count is now 241, ArkTS is 283, and the combined recorded count is 524. The matching-system metadata and recorded-versus-offline summary have been refreshed. DA/EXACT/LOC verdicts and their statistical comparisons are unchanged.
