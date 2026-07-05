#!/usr/bin/env python3
"""
run_ai_review.py —— 数据校对主入口

一条命令：提取闭区间 + 按一致性分类 + 调 AI 填充 human_review → 输出可读 .md 文件。

用法:
    python3 scripts/run_ai_review.py --start 1 --end 5
    python3 scripts/run_ai_review.py --start 1 --end 5 --no-ai
    python3 scripts/run_ai_review.py
"""

import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import API_CONFIG, PATHS, REVIEW_CONFIG, CONSISTENCY_PROMPTS, CONSISTENCY_LABELS


# ═══════════════════════════════════════════════════════════
# 一致性判定
# ═══════════════════════════════════════════════════════════

def classify_consistency(record: dict) -> str:
    objectives = record.get("auto_reject", {}).get("consensus", {}).get("objectives", {})
    models = REVIEW_CONFIG["valid_solver_names"]
    vals = [objectives.get(m) for m in models]
    present = [v for v in vals if v is not None]
    if len(present) < 3:
        return "missing"
    unique = set(present)
    if len(unique) == 1:
        return "agree_all"
    elif len(unique) == 2:
        return "agree_two"
    else:
        return "disagree_all"


def build_review_md(record: dict, category: str, line_index: int) -> str:
    """生成审阅 .md 文件内容。"""
    q = record["question"]
    ar = record["auto_reject"]
    rg = record["review_guidance"]
    consensus = ar.get("consensus", {})
    pid = record["problem_id"]
    # 取 AI 填充的 human_review（如有），否则用默认
    hr = record.get("human_review", copy.deepcopy(REVIEW_CONFIG["human_review_default"]))
    if not hr or hr.get("problem_valid") is None:
        hr = copy.deepcopy(REVIEW_CONFIG["human_review_default"])

    lines = [
        "---",
        f"problem_id: {pid}",
        f"category: {category}",
        f"line_index: {line_index}",
        "---",
        "",
        f"# 题目审查: {pid}",
        "",
        f"**分类**: {CONSISTENCY_LABELS.get(category, category)}",
        "",
        "## 题目",
        "",
        q.get("problem", ""),
        "",
        "## 拒绝原因 & 共识",
        "",
    ]

    # 共识表格
    lines.append("| 模型 | 状态 | 目标值 |")
    lines.append("|------|------|--------|")
    objectives = consensus.get("objectives", {})
    statuses = consensus.get("statuses", {})
    for m in REVIEW_CONFIG["valid_solver_names"]:
        lines.append(f"| {m} | {statuses.get(m, 'N/A')} | {objectives.get(m, 'N/A')} |")
    lines.append("")

    lines.append(f"**拒绝原因**: {'; '.join(ar.get('reject_reasons', []))}")
    lines.append("")

    # 审查指导
    lines.append("## 审查指导")
    lines.append("")
    lines.append(f"> {rg.get('core_instruction', '')}")
    lines.append("")
    for f in rg.get("focus", []):
        lines.append(f"- {f}")
    lines.append("")

    # 模型轨迹
    lines.append("## 模型求解轨迹")
    lines.append("")

    for mr in record["model_responses"]:
        solver = mr["solver_name"]
        cot = mr.get("cot_chain", "")
        lines.append(f"### {solver}")
        lines.append("")
        lines.append(cot)
        lines.append("")

    # corrected_reasoning_trace (可编辑段落)
    reasoning = hr.get("corrected_reasoning_trace", "").strip()
    lines.append("---")
    lines.append("")
    lines.append("## corrected_reasoning_trace")
    lines.append("")
    if reasoning:
        lines.append(reasoning)
    else:
        lines.append("*(在此填写修正后的完整推理思路，可直接换行)*")
    lines.append("")

    # corrected_code (可编辑代码块)
    code = hr.get("corrected_code", "").strip()
    lines.append("## corrected_code")
    lines.append("")
    lines.append("```python")
    if code:
        lines.append(code)
    else:
        lines.append("# 在此粘贴修正后的可运行 COPT 代码")
    lines.append("```")
    lines.append("")

    # human_review (仅短字段，不含 reasoning/code)
    short_hr = {k: v for k, v in hr.items() if k not in ("corrected_reasoning_trace", "corrected_code")}
    lines.append("## human_review")
    lines.append("")
    lines.append("> ⚠️ 推理思路和代码已在上方独立段落中编辑。此处只填决策字段。")
    lines.append("> `corrected_execution_result` 为 null 时需要人工运行代码后填写。")
    lines.append("")
    lines.append("```review_json")
    lines.append(json.dumps(short_hr, indent=2, ensure_ascii=False))
    lines.append("```")

    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════
# AI 调用
# ═══════════════════════════════════════════════════════════

def load_prompt(category: str) -> str:
    filename = CONSISTENCY_PROMPTS.get(category)
    if not filename:
        return ""
    prompt_path = os.path.join(PATHS["prompts_dir"], filename)
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def build_ai_message(rec: dict, category: str) -> str:
    q = rec["question"]
    ar = rec["auto_reject"]
    rg = rec["review_guidance"]
    consensus = ar.get("consensus", {})

    parts = [f"【{CONSISTENCY_LABELS.get(category, category)}】", ""]
    parts.append("## 题目\n" + q.get("problem", "") + "\n")
    parts.append("## 求解共识")
    objectives = consensus.get("objectives", {})
    statuses = consensus.get("statuses", {})
    for m in REVIEW_CONFIG["valid_solver_names"]:
        parts.append(f"- {m}: status={statuses.get(m, 'N/A')}, objective={objectives.get(m, 'N/A')}")
    parts.append(f"- 拒绝原因: {'; '.join(ar.get('reject_reasons', []))}\n")
    parts.append("## 审阅指导\n" + rg.get("core_instruction", ""))
    for f in rg.get("focus", []):
        parts.append(f"- {f}")
    parts.append("\n## 模型求解轨迹")
    for mr in rec["model_responses"]:
        parts.append(f"### {mr['solver_name']}\n" + mr.get("cot_chain", "") + "\n")
    return "\n".join(parts)


def call_ai(system_prompt: str, user_message: str) -> str:
    payload = {
        "model": API_CONFIG["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": API_CONFIG["max_tokens"],
        "temperature": API_CONFIG["temperature"],
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_CONFIG['api_key']}"}
    url = f"{API_CONFIG['base_url']}/chat/completions"
    last_error = None
    for attempt in range(API_CONFIG["max_retries"]):
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=API_CONFIG["timeout_sec"]) as resp:
                return json.loads(resp.read().decode("utf-8"))["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            last_error = f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}"
        except Exception as e:
            last_error = str(e)
        if attempt < API_CONFIG["max_retries"] - 1:
            time.sleep(2 ** attempt)
    raise RuntimeError(last_error)


def parse_ai_json(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"无法解析 AI 返回: {text[:200]}...")


def sanitize_human_review(hr: dict, pid: str) -> list:
    warnings = []
    default = copy.deepcopy(REVIEW_CONFIG["human_review_default"])
    for k in default:
        if k not in hr:
            hr[k] = default[k]
    for f in ("problem_valid", "final_accept"):
        if hr.get(f) is not None and not isinstance(hr[f], bool):
            hr[f] = None; warnings.append(f"{pid}: {f} 已置 null")
    valid = REVIEW_CONFIG["valid_solver_names"]
    wmn = hr.get("wrong_model_names", [])
    if not isinstance(wmn, list):
        hr["wrong_model_names"] = []
    else:
        hr["wrong_model_names"] = [n for n in wmn if n in valid]
    if hr.get("reviewer_group") != REVIEW_CONFIG["reviewer_group"]:
        hr["reviewer_group"] = REVIEW_CONFIG["reviewer_group"]
    if hr.get("corrected_execution_result") is not None:
        hr["corrected_execution_result"] = None
    return warnings


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="数据校对主入口：提取 + 分类 + AI → .md 文件")
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=None)
    parser.add_argument("--no-ai", action="store_true")
    args = parser.parse_args()

    input_path = PATHS["input_jsonl"]
    work_dir = PATHS["work_dir"]
    os.makedirs(work_dir, exist_ok=True)

    if not os.path.exists(input_path):
        print(f"❌ 找不到: {input_path}"); sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    total = len(all_lines)
    start_idx = args.start - 1
    end_idx = min((args.end if args.end else total) - 1, total - 1)
    count = end_idx - start_idx + 1

    print(f"📂 {input_path}  |  📊 {total} 行  |  📝 [{args.start}, {end_idx + 1}]  ({count} 条)  |  🤖 {'启用' if not args.no_ai else '禁用'}\n")

    # 解析 + 分类
    buckets = {"agree_all": [], "agree_two": [], "disagree_all": [], "missing": []}
    parse_fail = 0
    for i in range(start_idx, end_idx + 1):
        try:
            rec = json.loads(all_lines[i])
        except json.JSONDecodeError:
            parse_fail += 1; continue
        buckets[classify_consistency(rec)].append((rec, i))

    # 打印摘要
    print("━" * 50)
    print("📊 分类统计:")
    for cat in ["agree_all", "agree_two", "disagree_all", "missing"]:
        print(f"   {cat:15s} ({CONSISTENCY_LABELS[cat]})：{len(buckets[cat])} 条")
    print(f"   {'合计':15s}   {count - parse_fail} 条（解析失败 {parse_fail}）")
    print("━" * 50)

    # AI 填充
    if not args.no_ai:
        ai_ok = ai_fail = 0
        for cat in ["agree_all", "agree_two", "disagree_all", "missing"]:
            items = buckets[cat]
            if not items:
                continue
            prompt = load_prompt(cat)
            print(f"\n🤖 [{CONSISTENCY_LABELS[cat]}] {len(items)} 条")
            for idx, (rec, i) in enumerate(items):
                pid = rec["problem_id"]
                try:
                    user_msg = build_ai_message(rec, cat)
                    hr = parse_ai_json(call_ai(prompt, user_msg))
                    hr = sanitize_human_review(hr, pid)[1] if False else hr
                    sanitize_human_review(hr, pid)
                    rec["_ai_hr"] = hr  # 暂存
                    ai_ok += 1
                    print(f"   [{idx + 1}/{len(items)}] {pid} ✅")
                except Exception as e:
                    ai_fail += 1
                    print(f"   [{idx + 1}/{len(items)}] {pid} ❌ {e}")
        print(f"\n🤖 完成: {ai_ok}/{ai_ok + ai_fail} 成功, {ai_fail} 失败")

    # 写入 .md 文件
    for cat in ["agree_all", "agree_two", "disagree_all", "missing"]:
        cat_dir = os.path.join(work_dir, cat)
        os.makedirs(cat_dir, exist_ok=True)
        for rec, i in buckets[cat]:
            pid = rec["problem_id"]
            # 如果 AI 填充了 human_review，替换默认值
            if "_ai_hr" in rec:
                rec["human_review"] = rec.pop("_ai_hr")
            md = build_review_md(rec, cat, i)
            filepath = os.path.join(cat_dir, f"{pid}.md")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(md)

    # 打印输出
    print()
    for cat in ["agree_all", "agree_two", "disagree_all", "missing"]:
        if buckets[cat]:
            print(f"📁 work/{cat}/  ({CONSISTENCY_LABELS[cat]})：{len(buckets[cat])} 个 .md 文件")

    print(f"\n✅ 完成！共 {count - parse_fail} 个 .md 文件 → work/")
    print()
    print("🔜 下一步: 逐题打开 .md 人工复核，编辑底部的 review_json 代码块，然后:")
    print("   python3 scripts/replace_human_review.py --batch --start 1 --end 50")


if __name__ == "__main__":
    main()
