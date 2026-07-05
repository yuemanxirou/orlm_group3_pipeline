#!/usr/bin/env python3
"""
extract_review_item.py —— 按 problem_id 抽取单题到可读 .md 文件

用法:
    python3 scripts/extract_review_item.py --problem-id v_ff0f36fb6000ab
    python3 scripts/extract_review_item.py --problem-id v_ff0f36fb6000ab --output /tmp/review.md
"""

import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PATHS, REVIEW_CONFIG


def main():
    parser = argparse.ArgumentParser(description="按 problem_id 抽取单题到 .md")
    parser.add_argument("--problem-id", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    input_path = PATHS["input_jsonl"]
    if not os.path.exists(input_path):
        print(f"❌ 找不到: {input_path}"); sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("problem_id") != args.problem_id:
                continue

            q = rec["question"]
            ar = rec["auto_reject"]
            rg = rec["review_guidance"]
            consensus = ar.get("consensus", {})
            pid = rec["problem_id"]

            lines = [
                "---",
                f"problem_id: {pid}",
                f"line_index: {i}",
                "---",
                "",
                f"# 题目审查: {pid}",
                "",
                "## 题目",
                "",
                q.get("problem", ""),
                "",
                "## 拒绝原因 & 共识",
                "",
                "| 模型 | 状态 | 目标值 |",
                "|------|------|--------|",
            ]
            objectives = consensus.get("objectives", {})
            statuses = consensus.get("statuses", {})
            for m in REVIEW_CONFIG["valid_solver_names"]:
                lines.append(f"| {m} | {statuses.get(m, 'N/A')} | {objectives.get(m, 'N/A')} |")
            lines.append("")
            lines.append(f"**拒绝原因**: {'; '.join(ar.get('reject_reasons', []))}")
            lines.append("")
            lines.append("## 审查指导")
            lines.append("")
            lines.append(f"> {rg.get('core_instruction', '')}")
            lines.append("")
            for f in rg.get("focus", []):
                lines.append(f"- {f}")
            lines.append("")

            lines.append("## 模型求解轨迹")
            lines.append("")
            for mr in rec["model_responses"]:
                lines.append(f"### {mr['solver_name']}")
                lines.append("")
                lines.append(mr.get("cot_chain", ""))
                lines.append("")

            # corrected_reasoning_trace
            lines.append("---")
            lines.append("")
            lines.append("## corrected_reasoning_trace")
            lines.append("")
            lines.append("*(在此填写修正后的完整推理思路)*")
            lines.append("")
            # corrected_code
            lines.append("## corrected_code")
            lines.append("")
            lines.append("```python")
            lines.append("# 在此粘贴修正后的可运行 COPT 代码")
            lines.append("```")
            lines.append("")
            # human_review (仅短字段)
            hr = copy.deepcopy(REVIEW_CONFIG["human_review_default"])
            short_hr = {k: v for k, v in hr.items() if k not in ("corrected_reasoning_trace", "corrected_code")}
            lines.append("## human_review")
            lines.append("")
            lines.append("```review_json")
            lines.append(json.dumps(short_hr, indent=2, ensure_ascii=False))
            lines.append("```")

            out_path = args.output or os.path.join(PATHS["work_dir"], f"{pid}.md")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as out:
                out.write("\n".join(lines) + "\n")

            print(f"✅ 已抽取: {pid}")
            print(f"📄 {out_path}")
            return

    print(f"❌ 未找到: {args.problem_id}")
    sys.exit(1)


if __name__ == "__main__":
    main()
