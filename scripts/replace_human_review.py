#!/usr/bin/env python3
"""
replace_human_review.py —— 将单题 human_review 写回原始 JSONL

用法:
    python3 scripts/replace_human_review.py --problem-id v_ff0f36fb6000ab --human-review work/v_ff0f36fb6000ab.json
    python3 scripts/replace_human_review.py --problem-id v_ff0f36fb6000ab --human-review work/v_ff0f36fb6000ab.json --dry-run
"""

import argparse
import json
import os
import sys
import copy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PATHS, REVIEW_CONFIG


def validate_human_review(hr: dict, pid: str) -> list:
    """校验 human_review，返回错误列表。"""
    errors = []
    valid_names = REVIEW_CONFIG["valid_solver_names"]

    # 必填字段
    for field in REVIEW_CONFIG["human_review_fields"]:
        if field not in hr:
            errors.append(f"缺少字段: {field}")

    pv = hr.get("problem_valid")
    fa = hr.get("final_accept")

    if pv is not None and not isinstance(pv, bool):
        errors.append("problem_valid 必须为 true/false/null")
    if fa is not None and not isinstance(fa, bool):
        errors.append("final_accept 必须为 true/false/null")

    if fa is True:
        if not hr.get("corrected_reasoning_trace"):
            errors.append("final_accept=true 时 corrected_reasoning_trace 不能为空")
        if not hr.get("corrected_code"):
            errors.append("final_accept=true 时 corrected_code 不能为空")
    if fa is False:
        if not hr.get("notes"):
            errors.append("final_accept=false 时 notes 不能为空")

    if pv is False:
        if hr.get("corrected_reasoning_trace"):
            errors.append("problem_valid=false 时 corrected_reasoning_trace 应为空")
        if hr.get("corrected_code"):
            errors.append("problem_valid=false 时 corrected_code 应为空")

    for name in hr.get("wrong_model_names", []):
        if name not in valid_names:
            errors.append(f"wrong_model_names 含无效模型 '{name}'")

    if hr.get("reviewer_group") != REVIEW_CONFIG["reviewer_group"]:
        errors.append(f"reviewer_group 应为 '{REVIEW_CONFIG['reviewer_group']}'")

    return errors


def main():
    parser = argparse.ArgumentParser(description="单题 human_review 写回原始 JSONL")
    parser.add_argument("--problem-id", required=True, help="problem_id")
    parser.add_argument("--human-review", required=True, help="包含 human_review 的 JSON 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只校验不写文件")
    args = parser.parse_args()

    input_path = PATHS["input_jsonl"]
    if not os.path.exists(input_path):
        print(f"❌ 找不到: {input_path}")
        sys.exit(1)

    if not os.path.exists(args.human_review):
        print(f"❌ 找不到: {args.human_review}")
        sys.exit(1)

    # 读取 human_review
    with open(args.human_review, "r", encoding="utf-8") as f:
        data = json.load(f)

    hr = data.get("human_review") if isinstance(data, dict) else None
    if not hr:
        print("❌ 文件中未找到 human_review 字段（请确认格式为 {\"human_review\": {...}}）")
        sys.exit(1)

    # 校验
    errors = validate_human_review(hr, args.problem_id)
    if errors:
        print("❌ 校验失败:")
        for e in errors:
            print(f"   - {e}")
        if args.dry_run:
            sys.exit(1)
        print()
        print("⚠️  仍有错误，是否继续写入？(y/N)", end=" ", flush=True)
        answer = input().strip().lower()
        if answer != "y":
            print("已取消")
            sys.exit(1)

    if args.dry_run:
        print("✅ 校验通过（dry-run，未写入）")
        return

    # 替换
    pid = args.problem_id
    updated = False

    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("problem_id") == pid:
            # 只替换 human_review
            rec["human_review"] = hr
            lines[i] = json.dumps(rec, ensure_ascii=False) + "\n"
            updated = True
            break

    if not updated:
        print(f"❌ 在原始文件中未找到: {pid}")
        sys.exit(1)

    # 写回（原地修改）
    with open(input_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"✅ 已写回: {pid}（第 {i + 1} 行）")


if __name__ == "__main__":
    main()
