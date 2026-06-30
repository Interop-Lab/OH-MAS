"""
消融实验子集抽取脚本（可复现）

算法：
1. 按 rule_id 分组，每组内按 instance_id 升序排列
2. 各组配额 = max(1, round(组大小 / 总数 × n))
   若配额之和 != n，按规则组大小降序微调
3. 以 random.Random(seed) 按规则名升序依次对每组执行 sample()

用法：
    python scripts/sample_ablation_subset.py
"""

import json
import random
import math
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent

def stratified_sample(data, n, seed):
    rng = random.Random(seed)

    rule_groups = defaultdict(list)
    for item in data:
        rule_groups[item['rule_id']].append(item['instance_id'])
    for rule in rule_groups:
        rule_groups[rule] = sorted(rule_groups[rule])

    total = len(data)
    sorted_rules = sorted(rule_groups.keys())

    quotas = {rule: max(1, round(len(rule_groups[rule]) / total * n)) for rule in sorted_rules}

    diff = n - sum(quotas.values())
    rules_by_size = sorted(sorted_rules, key=lambda r: -len(rule_groups[r]))
    for rule in rules_by_size:
        if diff == 0:
            break
        if diff > 0:
            quotas[rule] += 1
            diff -= 1
        elif quotas[rule] > 1:
            quotas[rule] -= 1
            diff += 1

    selected_ids = set()
    for rule in sorted_rules:
        items = rule_groups[rule]
        k = quotas[rule]
        sampled = rng.sample(items, min(k, len(items)))
        selected_ids.update(sampled)

    result = [item for item in data if item['instance_id'] in selected_ids]
    result.sort(key=lambda x: x['instance_id'])
    return result, quotas


def main():
    seed = 42
    n = 100

    with open(ROOT / 'data' / 'arkts_dataset_final.json', encoding='utf-8') as f:
        arkts_data = json.load(f)
    with open(ROOT / 'data' / 'cpp_dataset_final.json', encoding='utf-8') as f:
        cpp_data = json.load(f)

    arkts_subset, arkts_quotas = stratified_sample(arkts_data, n, seed)
    cpp_subset, cpp_quotas = stratified_sample(cpp_data, n, seed)

    with open(ROOT / 'data' / 'arkts_ablation_subset.json', 'w', encoding='utf-8') as f:
        json.dump(arkts_subset, f, ensure_ascii=False, indent=2)

    with open(ROOT / 'data' / 'cpp_ablation_subset.json', 'w', encoding='utf-8') as f:
        json.dump(cpp_subset, f, ensure_ascii=False, indent=2)

    print(f"seed={seed}, n={n}")
    print(f"ArkTS: {len(arkts_subset)} instances, quotas: {dict(sorted(arkts_quotas.items()))}")
    print(f"C++:   {len(cpp_subset)} instances, quotas: {dict(sorted(cpp_quotas.items()))}")
    print(f"\nArkTS IDs: {', '.join(item['instance_id'] for item in arkts_subset)}")
    print(f"\nC++ IDs:   {', '.join(item['instance_id'] for item in cpp_subset)}")


if __name__ == '__main__':
    main()
