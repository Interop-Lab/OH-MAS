"""
人工复审子集抽取脚本（简单随机抽样）

从完整数据集各随机抽取 50 个实例，合计 100 个，用于人工复审。

用法：
    python scripts/sample_manual_review.py
"""

import json
import random
from pathlib import Path

ROOT = Path(__file__).parent.parent

def main():
    seed = 42
    n = 50

    with open(ROOT / 'data' / 'arkts_dataset_final.json', encoding='utf-8') as f:
        arkts_data = json.load(f)
    with open(ROOT / 'data' / 'cpp_dataset_final.json', encoding='utf-8') as f:
        cpp_data = json.load(f)

    rng = random.Random(seed)
    arkts_subset = rng.sample(arkts_data, n)
    cpp_subset = rng.sample(cpp_data, n)

    arkts_subset.sort(key=lambda x: x['instance_id'])
    cpp_subset.sort(key=lambda x: x['instance_id'])

    with open(ROOT / 'data' / 'arkts_manual_review_subset.json', 'w', encoding='utf-8') as f:
        json.dump(arkts_subset, f, ensure_ascii=False, indent=2)
    with open(ROOT / 'data' / 'cpp_manual_review_subset.json', 'w', encoding='utf-8') as f:
        json.dump(cpp_subset, f, ensure_ascii=False, indent=2)

    print(f"seed={seed}, n={n}")
    print(f"ArkTS: {len(arkts_subset)} instances")
    print(f"C++:   {len(cpp_subset)} instances")
    print(f"\nArkTS IDs: {', '.join(x['instance_id'] for x in arkts_subset)}")
    print(f"\nC++ IDs:   {', '.join(x['instance_id'] for x in cpp_subset)}")


if __name__ == '__main__':
    main()
