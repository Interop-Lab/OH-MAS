# Matcher notes (matchers.py, test_matchers.py: 15 tests pass)
Versions: DA_MIXED = evaluator count_new_warnings (unchanged). EXACT = raw set difference. LOC-v1 = verbatim copy of rescore_location_aware.new_warnings_location_aware("hunk"). LOC-v2 = LOC-v1 + one change (D1).

Verified OK in LOC-v1 (unit tests): shift by unrelated insertion; real removal; pure-insertion boundary lines; in-hunk rewrite absorbed; a removed baseline warning absorbs at most ONE post warning (Counter pool; also with two baselines and three post); different rule never absorbed; rename (hunkless) maps baseline to new path; new-file / deleted-file; line-0 project alarms stay on the same file.
Blind spot of DA_MIXED shown by test_new_same_rule_blind_spot (new same-rule warning in a patched file is accepted by DA_MIXED, reported by EXACT and LOC).

## D1 (defect, LOC-v1): path lookup is a boundary-less suffix match
`_find()` accepts `path.endswith(k) or k.endswith(path)` (and then requires a unique hit). A patch to root `a.ets` makes baseline warnings of the DIFFERENT file `x/a.ets` map through a.ets's hunks, so unchanged `x/a.ets` warnings are reported as new (false SECONDARY_DEFECT). Tests: test_D1_v1_exhibits_defect (passes = defect shown), test_D1_v2_correct. LOC-v2 uses exact path match only (canonical diff and warning paths are both repo-relative). Pre-registered rule: LOC-v1 stays the reported default; because a unit-tested defect exists, results must be reported for both v1 and v2 (the engine/matcher computes both from the same warnings); the choice is made on the rule, not on outcomes. Practical impact is limited to projects with same-named files in different directories where one is patched.
Same boundary-less suffix test exists in the historic DA_MIXED (patched-file test); documented (test_D1_boundaryless_in_evaluator_too), NOT changed (it is the baseline under study).

## Known LOC limitations (not defects, by design; reported, not fixed)
- Cross-hunk moves: a baseline warning removed in one hunk and re-emitted on an added line of a different hunk is NOT absorbed (test_cross_hunk_move_is_limitation). Moves into another file likewise.
- Project-level alarms (line 0) that relocate to another file (e.g. OH_EXT_N001) are not matched.
- Post warnings on context lines or unchanged lines whose line number did not map to a baseline warning count as new.
- Hunk absorption needs a post warning on an ADDED line; a rewritten line that keeps the alarm only at a different (unadded) line is new.
