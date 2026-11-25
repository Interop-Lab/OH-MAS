#!/usr/bin/env python3
"""Paired OH-MAS vs HapRepair comparison, clustered by file snapshot (project@commit:target_file).
Statistic: SPR difference OH-MAS - HapRepair in pp. CI: cluster bootstrap (resample file-snapshot clusters with replacement, B=20000, percentile 95%).
p: two-sided cluster sign-flip permutation test on per-cluster paired differences (20000 draws). Fixed seed 20261117. Needs numpy only.
Usage (from the artifact root): python3 scripts/paired_stats.py   -> results/paired_stats.json"""
import csv, json, os, collections
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def stats(rows, a_col, b_col, label, B=20000, seed=20261117):
    g = collections.defaultdict(list)
    for r in rows: g[r['file_snapshot_id']].append(r)
    keys = sorted(g); n = len(rows)
    a = np.array([sum(int(r[a_col]) for r in g[k]) for k in keys], float)
    b = np.array([sum(int(r[b_col]) for r in g[k]) for k in keys], float)
    m = np.array([len(g[k]) for k in keys], float)
    d = (a.sum() - b.sum()) / n * 100
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(keys), (B, len(keys)))
    bs = (a[idx].sum(1) - b[idx].sum(1)) / m[idx].sum(1) * 100
    lo, hi = np.percentile(bs, [2.5, 97.5])
    dk = a - b; sg = rng.choice([-1, 1], (B, len(keys)))
    p = ((np.abs((sg * dk).sum(1)) >= abs(dk.sum()) - 1e-9).sum() + 1) / (B + 1)
    return dict(label=label, n=n, clusters=len(keys), ohmas_pass=int(a.sum()), hap_pass=int(b.sum()),
                ohmas_spr=round(float(a.sum() / n * 100), 1), hap_spr=round(float(b.sum() / n * 100), 1),
                diff_pp=round(float(d), 1), ci95=[round(float(lo), 1), round(float(hi), 1)], p=round(float(p), 4))

ob = list(csv.DictReader(open(os.path.join(ROOT, 'paired_per_instance_ohbench382.csv'))))
ex = list(csv.DictReader(open(os.path.join(ROOT, 'paired_per_instance_external100.csv'))))
out = [stats(ob, 'ohmas_p1', 'hap_original', 'OH-Bench 382: OH-MAS (P1 recorded) vs public HapRepair (original)'),
       stats(ob, 'ohmas_p1', 'hap_extended', 'OH-Bench 382: OH-MAS (P1 recorded) vs extended-rule-coverage HapRepair (not the public release)'),
       stats(ex, 'ohmas_external', 'hap_paper_protocol', 'External 100: OH-MAS vs HapRepair (paper protocol)')]
new = [r for r in ob if r['is_new44'] == '1']
out.append(dict(label='new 44 tasks', n=len(new), hap_extended_pass=sum(int(r['hap_extended']) for r in new), ohmas_pass=sum(int(r['ohmas_p1']) for r in new)))
os.makedirs(os.path.join(ROOT, 'results'), exist_ok=True)
json.dump(out, open(os.path.join(ROOT, 'results', 'paired_stats.json'), 'w'), indent=1)
for o in out: print(o)
