#!/usr/bin/env python3
"""
writeback.py —— 将 work/ 下的审阅结果写回原始 JSONL + 校验

用法:
    python3 scripts/writeback.py
    python3 scripts/writeback.py --start 1 --end 123
    python3 scripts/writeback.py --validate-only        # 仅校验已输出的文件
"""

import argparse
import json
import os
import sys
import copy

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PATHS, REVIEW_CONFIG


def collect_reviews(work_dir: str) -> dict:
    """扫描 work/ 下所有 .json 文件，收集 human_review 更新。
    返回 {problem_id: human_review}。"""
    updates = {}
    for fname in os.listdir(work_dir):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(work_dir, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                records = json.load(f)
        except (json.JSONDecodeError, Exception) as e:
            print(f"⚠️  跳过 {fname}: {e}")
            continue

        for rec in records:
            pid = rec.get("problem_id")
            hr = rec.get("human_review")
            if pid and hr:
                updates[pid] = hr

    return updates


def validate_output(output_path: str, original_path: str = None) -> tuple:
    """校验输出 JSONL，返回 (errors, warnings, stats)。"""
    errors = []
    warnings = []
    stats = {"total": 0, "parse_err": 0, "accept_true": 0, "accept_false": 0,
             "accept_null": 0, "valid_true": 0, "valid_false": 0, "valid_null": 0,
             "integrity": 0}

    original_records = None
    if original_path and os.path.exists(original_path):
        with open(original_path, "r", encoding="utf-8") as f:
            original_records = []
            for line in f:
                try:
                    original_records.append(json.loads(line))
                except json.JSONDecodeError:
                    original_records.append(None)

    if not os.path.exists(output_path):
        return [f"文件不存在: {output_path}"], [], stats

    with open(output_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    stats["total"] = len(lines)
    expected = REVIEW_CONFIG["expected_line_count"]
    if len(lines) != expected:
        errors.append(f"行数不匹配: 期望 {expected}，实际 {len(lines)}")

    schema = REVIEW_CONFIG["human_review_fields"]
    valid_names = REVIEW_CONFIG["valid_solver_names"]

    for i, line in enumerate(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"第 {i + 1} 行 JSON 解析失败: {e}")
            stats["parse_err"] += 1
            continue

        pid = rec.get("problem_id", f"L{i + 1}")
        hr = rec.get("human_review")
        if not hr:
            warnings.append(f"{pid}: 缺少 human_review")
            continue

        # 字段完整性
        for f in schema:
            if f not in hr:
                errors.append(f"{pid}: human_review 缺少字段 '{f}'")

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
            if hr.get("corrected_execution_result") is None:
                warnings.append(f"{pid}: final_accept=true 但 execution_result 为 null（需人工运行代码后填写）")
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

        # 完整性（对比原始）
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
    parser = argparse.ArgumentParser(description="写回审阅结果 + 校验")
    parser.add_argument("--start", type=int, default=None, help="闭区间起始（用于文件命名）")
    parser.add_argument("--end", type=int, default=None, help="闭区间结束（用于文件命名）")
    parser.add_argument("--validate-only", action="store_true", help="仅校验已有输出")
    args = parser.parse_args()

    work_dir = PATHS["work_dir"]
    output_dir = PATHS["output_dir"]
    input_path = PATHS["input_jsonl"]

    if not os.path.exists(input_path):
        print(f"❌ 找不到原始文件: {input_path}")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        original_lines = f.readlines()

    # 解析原始
    original_records = []
    pid_to_idx = {}
    for i, line in enumerate(original_lines):
        try:
            rec = json.loads(line)
            original_records.append(rec)
            pid_to_idx[rec["problem_id"]] = i
        except json.JSONDecodeError:
            original_records.append(None)

    # 收集更新
    if not args.validate_only:
        updates = collect_reviews(work_dir)
        print(f"📂 扫描 work/ 目录")
        print(f"📝 收集到 {len(updates)} 条 human_review 更新")

        if not updates:
            print("⚠️  未发现任何更新，请先运行 run_ai_review.py")
            sys.exit(0)

        # 应用更新
        output_records = copy.deepcopy(original_records)
        updated = 0
        for pid, hr in updates.items():
            if pid in pid_to_idx:
                idx = pid_to_idx[pid]
                output_records[idx]["human_review"] = hr
                updated += 1
            else:
                print(f"⚠️  {pid}: 在原始文件中未找到，跳过")

        # 写入
        os.makedirs(output_dir, exist_ok=True)
        total_lines = len(original_lines)
        s = args.start if args.start else 1
        e = args.end if args.end else total_lines
        fname = f"group_3_reviewed_{s}_{e}.jsonl"
        output_path = os.path.join(output_dir, fname)

        with open(output_path, "w", encoding="utf-8") as f:
            for rec in output_records:
                if rec is not None:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        print(f"📄 输出: {output_path}（{updated} 条已更新）")
        print()
    else:
        # 找已有输出文件
        output_path = None
        if os.path.exists(output_dir):
            for f in sorted(os.listdir(output_dir), reverse=True):
                if f.startswith("group_3_reviewed_") and f.endswith(".jsonl"):
                    output_path = os.path.join(output_dir, f)
                    break
        if not output_path:
            print("❌ 未找到已输出的文件，请先运行 writeback 写入")
            sys.exit(1)

    # 校验
    print("━" * 50)
    print("🔍 校验...")
    errors, warnings, stats = validate_output(output_path, input_path)

    print(f"\n📊 统计:")
    print(f"   总行数: {stats['total']}")
    print(f"   JSON 解析错误: {stats['parse_err']}")
    print(f"   final_accept=true: {stats['accept_true']}")
    print(f"   final_accept=false: {stats['accept_false']}")
    print(f"   final_accept=null（未审）: {stats['accept_null']}")
    print(f"   problem_valid=true: {stats['valid_true']}")
    print(f"   problem_valid=false: {stats['valid_false']}")
    print(f"   problem_valid=null（未审）: {stats['valid_null']}")
    print(f"   完整性警告: {stats['integrity']}")

    if warnings:
        print(f"\n⚠️  警告 ({len(warnings)}):")
        for w in warnings[:15]:
            print(f"   - {w}")
        if len(warnings) > 15:
            print(f"   ... 还有 {len(warnings) - 15} 条")

    if errors:
        print(f"\n❌ 错误 ({len(errors)}):")
        for e in errors[:20]:
            print(f"   - {e}")
        if len(errors) > 20:
            print(f"   ... 还有 {len(errors) - 20} 条")
        print("\n❌ 校验未通过")
        sys.exit(1)
    else:
        print("\n✅ 校验通过！")


if __name__ == "__main__":
    main()
