#!/usr/bin/env python3
"""
replace_human_review.py —— 将 .md 审阅文件中的 human_review 写回原始 JSONL

用法:
    python3 scripts/replace_human_review.py --input work/agree_two/v_xxx.md
    python3 scripts/replace_human_review.py --batch
    python3 scripts/replace_human_review.py --batch --start 1 --end 50
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PATHS


def parse_review_json(content: str) -> dict:
    m = re.search(r"```review_json\s*\n(.*?)\n```", content, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def parse_section(content: str, heading: str) -> str:
    """提取 ## heading 之后到下一个 ## 或 --- 之前的内容。"""
    pattern = rf"## {heading}\s*\n\n?(.*?)(?=\n## |\n---|\Z)"
    m = re.search(pattern, content, re.DOTALL)
    if not m:
        return ""
    text = m.group(1).strip()
    # 如果是代码块，提取 python 代码
    code_m = re.match(r"```(?:python)?\s*\n(.*?)\n```", text, re.DOTALL)
    if code_m:
        return code_m.group(1).rstrip()
    return text


def build_human_review(content: str) -> dict:
    """从 .md 中组装完整 human_review。"""
    hr = parse_review_json(content) or {}
    reasoning = parse_section(content, "corrected_reasoning_trace")
    code = parse_section(content, "corrected_code")
    if reasoning and not hr.get("corrected_reasoning_trace"):
        hr["corrected_reasoning_trace"] = reasoning
    if code and not hr.get("corrected_code"):
        hr["corrected_code"] = code
    return hr


def collect_from_workdir(work_dir: str) -> dict:
    """扫描 work/ 下所有 .md，返回 {problem_id: human_review}。"""
    updates = {}
    found = 0
    for root, dirs, files in os.walk(work_dir):
        for fname in files:
            if not fname.endswith(".md") or fname.startswith("."):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
            # 从文件名或 frontmatter 获取 problem_id
            pid = os.path.basename(fpath).replace(".md", "")
            fm = re.match(r"---\s*\n.*?problem_id:\s*(\S+).*?\n---", content, re.DOTALL)
            if fm:
                pid = fm.group(1)
            hr = build_human_review(content)
            if not hr or "problem_valid" not in hr:
                continue
            # 跳过未审阅的
            if (hr.get("problem_valid") is None and hr.get("final_accept") is None
                    and not parse_section(content, "corrected_reasoning_trace")
                    and not parse_section(content, "corrected_code")):
                continue
            updates[pid] = hr
            found += 1
    print(f"📂 扫描 work/ → {found} 条已审阅")
    return updates


def main():
    parser = argparse.ArgumentParser(description="将 .md 中的 human_review 写回 JSONL")
    parser.add_argument("--input", default=None, help="单个 .md 文件路径")
    parser.add_argument("--batch", action="store_true", help="批量扫描 work/")
    parser.add_argument("--start", type=int, default=None, help="闭区间起始")
    parser.add_argument("--end", type=int, default=None, help="闭区间结束")
    args = parser.parse_args()

    input_path = PATHS["input_jsonl"]
    output_dir = PATHS["output_dir"]
    work_dir = PATHS["work_dir"]

    if not os.path.exists(input_path):
        print(f"❌ 找不到: {input_path}"); sys.exit(1)

    # 收集
    if args.batch:
        updates = collect_from_workdir(work_dir)
        if not updates:
            print("⚠️  未发现已审阅的 .md 文件"); sys.exit(0)
    elif args.input:
        if not os.path.exists(args.input):
            print(f"❌ 不存在: {args.input}"); sys.exit(1)
        with open(args.input, "r", encoding="utf-8") as f:
            hr = build_human_review(f.read())
        if not hr or "problem_valid" not in hr:
            print("❌ 文件中未找到 review_json 块"); sys.exit(1)
        pid = os.path.basename(args.input).replace(".md", "")
        updates = {pid: hr}
    else:
        parser.print_help(); sys.exit(1)

    # 写回
    with open(input_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    updated = 0
    for i, line in enumerate(lines):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        pid = rec.get("problem_id")
        if pid in updates:
            rec["human_review"] = updates[pid]
            lines[i] = json.dumps(rec, ensure_ascii=False) + "\n"
            updated += 1

    if updated == 0:
        print("❌ 未匹配到任何 problem_id"); sys.exit(1)

    os.makedirs(output_dir, exist_ok=True)
    total = len(lines)
    s = args.start if args.start else 1
    e = args.end if args.end else total
    output_path = os.path.join(output_dir, f"group_3_reviewed_{s}_{e}.jsonl")

    with open(output_path, "w", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"📄 已写回 {updated} 条 → {output_path}")
    print(f"🔜 校验: python3 scripts/validate_review_file.py --input {output_path}")


if __name__ == "__main__":
    main()
