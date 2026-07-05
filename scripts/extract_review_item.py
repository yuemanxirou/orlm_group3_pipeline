#!/usr/bin/env python3
"""
extract_review_item.py —— 按 problem_id 抽取单题到可读 JSON 文件

用法:
    python3 scripts/extract_review_item.py --problem-id v_ff0f36fb6000ab
    python3 scripts/extract_review_item.py --problem-id v_ff0f36fb6000ab --output /tmp/my_review.json
"""

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PATHS, REVIEW_CONFIG


def main():
    parser = argparse.ArgumentParser(description="按 problem_id 抽取单题到可读 JSON")
    parser.add_argument("--problem-id", required=True, help="要抽取的 problem_id")
    parser.add_argument("--output", default=None, help="输出文件路径（默认 work/<problem_id>.json）")
    args = parser.parse_args()

    input_path = PATHS["input_jsonl"]
    if not os.path.exists(input_path):
        print(f"❌ 找不到文件: {input_path}")
        sys.exit(1)

    # 按 problem_id 查找
    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("problem_id") == args.problem_id:
                # 只保留 5 个必要字段
                slim = {
                    "problem_id": rec["problem_id"],
                    "question": rec["question"],
                    "auto_reject": rec["auto_reject"],
                    "review_guidance": rec["review_guidance"],
                    "model_responses": rec["model_responses"],
                    "human_review": copy.deepcopy(REVIEW_CONFIG["human_review_default"]),
                }

                out_path = args.output or os.path.join(
                    PATHS["work_dir"], f"{args.problem_id}.json"
                )
                os.makedirs(os.path.dirname(out_path), exist_ok=True)

                with open(out_path, "w", encoding="utf-8") as out:
                    json.dump(slim, out, indent=2, ensure_ascii=False)

                print(f"✅ 已抽取: {args.problem_id}")
                print(f"📄 输出: {out_path}")
                print()
                print("字段说明:")
                print("  question.problem         — 原始题目")
                print("  auto_reject.reject_reasons — 拒绝原因")
                print("  review_guidance          — 只读审查提示")
                print("  model_responses[].cot_chain — 模型 CoT + 代码（可读换行）")
                print("  human_review             — 空模板，请编辑后运行 replace_human_review.py 写回")
                return

    print(f"❌ 未找到 problem_id: {args.problem_id}")
    sys.exit(1)


if __name__ == "__main__":
    main()
