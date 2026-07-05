#!/usr/bin/env python3
"""
validate_final.py —— 校验最终 JSONL 文件的格式和一致性

用法:
    python3 scripts/validate_final.py
    python3 scripts/validate_final.py --input output/group_3_reviewed_1_50.jsonl
    python3 scripts/validate_final.py --compare group_3.jsonl   # 同时对比原始文件
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PATHS, REVIEW_CONFIG


def validate(input_path: str, original_path: str = None) -> tuple:
    """返回 (errors, warnings, stats)。"""
    errors = []
    warnings = []
    stats = {
        "total": 0, "parse_err": 0, "no_hr": 0,
        "accept_true": 0, "accept_false": 0, "accept_null": 0,
        "valid_true": 0, "valid_false": 0, "valid_null": 0,
        "integrity": 0,
    }

    # 加载原始文件对比
    original_records = None
    if original_path and os.path.exists(original_path):
        with open(original_path, "r", encoding="utf-8") as f:
            original_records = []
            for line in f:
                try:
                    original_records.append(json.loads(line))
                except json.JSONDecodeError:
                    original_records.append(None)

    if not os.path.exists(input_path):
        return [f"文件不存在: {input_path}"], [], stats

    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    stats["total"] = len(lines)
    expected = REVIEW_CONFIG["expected_line_count"]
    valid_names = REVIEW_CONFIG["valid_solver_names"]

    if len(lines) != expected:
        errors.append(f"行数不匹配: 期望 {expected}，实际 {len(lines)}")

    for i, line in enumerate(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"第 {i + 1} 行: JSON 解析失败 — {e}")
            stats["parse_err"] += 1
            continue

        pid = rec.get("problem_id", f"L{i + 1}")
        hr = rec.get("human_review")

        if not hr:
            stats["no_hr"] += 1
            warnings.append(f"{pid}: 缺少 human_review")
            continue

        # 字段完整性
        for field in REVIEW_CONFIG["human_review_fields"]:
            if field not in hr:
                errors.append(f"{pid}: human_review 缺少字段 '{field}'")

        pv = hr.get("problem_valid")
        fa = hr.get("final_accept")

        if pv is True:
            stats["valid_true"] += 1
        elif pv is False:
            stats["valid_false"] += 1
        else:
            stats["valid_null"] += 1

        if fa is True:
            stats["accept_true"] += 1
            if not hr.get("corrected_reasoning_trace"):
                errors.append(f"{pid}: final_accept=true 但 corrected_reasoning_trace 为空")
            if not hr.get("corrected_code"):
                errors.append(f"{pid}: final_accept=true 但 corrected_code 为空")
        elif fa is False:
            stats["accept_false"] += 1
            if hr.get("problem_valid") is not None and not hr.get("notes"):
                errors.append(f"{pid}: final_accept=false 但 notes 为空")
        else:
            stats["accept_null"] += 1

        # wrong_model_names
        for name in hr.get("wrong_model_names", []):
            if name not in valid_names:
                errors.append(f"{pid}: wrong_model_names 含无效模型 '{name}'")

        # problem_valid=false 字段应为空
        if pv is False:
            if hr.get("corrected_reasoning_trace"):
                errors.append(f"{pid}: problem_valid=false 但 reasoning_trace 非空")
            if hr.get("corrected_code"):
                errors.append(f"{pid}: problem_valid=false 但 code 非空")
            if hr.get("corrected_execution_result") is not None:
                errors.append(f"{pid}: problem_valid=false 但 execution_result 非 null")

        # reviewer_group
        if hr.get("reviewer_group") != REVIEW_CONFIG["reviewer_group"]:
            errors.append(f"{pid}: reviewer_group 应为 '{REVIEW_CONFIG['reviewer_group']}'")

        # 完整性对比
        if original_records and i < len(original_records) and original_records[i] is not None:
            orig = original_records[i]
            for key in rec:
                if key == "human_review":
                    continue
                if key not in orig:
                    warnings.append(f"{pid}: 新增键 '{key}'")
                elif rec[key] != orig[key]:
                    stats["integrity"] += 1
                    warnings.append(f"{pid}: 非 human_review 字段 '{key}' 被修改")

    return errors, warnings, stats


def main():
    parser = argparse.ArgumentParser(description="校验最终 JSONL 文件")
    parser.add_argument("--input", default=None, help="目标 JSONL（默认搜索 output/ 下最新）")
    parser.add_argument("--compare", default=None, help="原始 JSONL 用于完整性对比")
    args = parser.parse_args()

    input_path = args.input
    if not input_path:
        output_dir = PATHS["output_dir"]
        if os.path.exists(output_dir):
            files = sorted(
                [f for f in os.listdir(output_dir) if f.endswith(".jsonl")],
                reverse=True,
            )
            if files:
                input_path = os.path.join(output_dir, files[0])
        if not input_path:
            print("❌ 未找到输出文件，请用 --input 指定")
            sys.exit(1)

    original_path = args.compare or PATHS["input_jsonl"]

    print(f"🔍 校验: {input_path}")
    if os.path.exists(original_path):
        print(f"📄 对比: {original_path}")
    print()

    errors, warnings, stats = validate(input_path, original_path)

    print("━" * 50)
    print("📊 统计:")
    print(f"   总行数:           {stats['total']}")
    print(f"   JSON 解析错误:    {stats['parse_err']}")
    print(f"   缺少 human_review: {stats['no_hr']}")
    print()
    print(f"   final_accept=true:  {stats['accept_true']}")
    print(f"   final_accept=false: {stats['accept_false']}")
    print(f"   final_accept=null:  {stats['accept_null']}（未审）")
    print()
    print(f"   problem_valid=true:  {stats['valid_true']}")
    print(f"   problem_valid=false: {stats['valid_false']}")
    print(f"   problem_valid=null:  {stats['valid_null']}（未审）")
    print()
    print(f"   完整性警告:         {stats['integrity']}")
    print("━" * 50)

    if warnings:
        print(f"\n⚠️  警告 ({len(warnings)}):")
        for w in warnings[:20]:
            print(f"   - {w}")
        if len(warnings) > 20:
            print(f"   ... 还有 {len(warnings) - 20} 条")

    if errors:
        print(f"\n❌ 错误 ({len(errors)}):")
        for e in errors[:30]:
            print(f"   - {e}")
        if len(errors) > 30:
            print(f"   ... 还有 {len(errors) - 30} 条")
        print("\n❌ 校验未通过")
        sys.exit(1)
    else:
        print("\n✅ 校验通过！")


if __name__ == "__main__":
    main()
