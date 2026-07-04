#!/usr/bin/env python3
"""
run_ai_review.py —— 数据校对主入口

一条命令：提取指定闭区间 + 按一致性分类 + 调 AI 填充 human_review → 输出可读 JSON。

用法:
    python3 scripts/run_ai_review.py --start 1 --end 5
    python3 scripts/run_ai_review.py --start 1 --end 5 --no-ai   # 只分类不调 AI
    python3 scripts/run_ai_review.py                              # 全量
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
    """判断单条记录的模型目标值一致性分类。"""
    objectives = record.get("auto_reject", {}).get("consensus", {}).get("objectives", {})
    models = REVIEW_CONFIG["valid_solver_names"]
    vals = [objectives.get(m) for m in models]
    # 排除 None（执行失败的模型）
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


# ═══════════════════════════════════════════════════════════
# AI 调用
# ═══════════════════════════════════════════════════════════

def load_prompt(category: str) -> str:
    """加载指定分类的 prompt 模板。"""
    filename = CONSISTENCY_PROMPTS.get(category)
    if not filename:
        return ""
    prompt_path = os.path.join(PATHS["prompts_dir"], filename)
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def build_user_message(record: dict, category: str) -> str:
    """构建发给 AI 的用户消息。"""
    q = record["question"]
    ar = record["auto_reject"]
    rg = record["review_guidance"]
    consensus = ar.get("consensus", {})

    parts = []

    # 分类标识
    parts.append(f"【分类】{CONSISTENCY_LABELS.get(category, category)}")
    parts.append("")

    # 题目
    parts.append("## 题目")
    parts.append(q.get("problem", ""))
    parts.append("")

    # 共识/拒绝信息
    parts.append("## 求解共识")
    objectives = consensus.get("objectives", {})
    statuses = consensus.get("statuses", {})
    for m in REVIEW_CONFIG["valid_solver_names"]:
        parts.append(f"- {m}: status={statuses.get(m, 'N/A')}, objective={objectives.get(m, 'N/A')}")
    parts.append(f"- 拒绝原因: {'; '.join(ar.get('reject_reasons', []))}")
    parts.append("")

    # 审阅指导
    parts.append("## 审阅指导")
    parts.append(rg.get("core_instruction", ""))
    focus = rg.get("focus", [])
    if focus:
        parts.append("审查重点:")
        for f in focus:
            parts.append(f"- {f}")
    parts.append("")

    # 模型轨迹
    parts.append("## 模型求解轨迹")
    for mr in record["model_responses"]:
        parts.append(f"### {mr['solver_name']}")
        parts.append(mr.get("cot_chain", ""))
        parts.append("")

    return "\n".join(parts)


def call_ai(system_prompt: str, user_message: str) -> str:
    """调用 AI API，返回响应文本。"""
    payload = {
        "model": API_CONFIG["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": API_CONFIG["max_tokens"],
        "temperature": API_CONFIG["temperature"],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_CONFIG['api_key']}",
    }
    url = f"{API_CONFIG['base_url']}/chat/completions"

    last_error = None
    for attempt in range(API_CONFIG["max_retries"]):
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=API_CONFIG["timeout_sec"]) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            last_error = f"HTTP {e.code}: {body}"
        except Exception as e:
            last_error = str(e)
        if attempt < API_CONFIG["max_retries"] - 1:
            time.sleep(2 ** attempt)

    raise RuntimeError(last_error)


def parse_ai_response(text: str, pid: str) -> dict:
    """从 AI 响应中提取 human_review JSON。"""
    # 尝试直接解析
    text = text.strip()
    try:
        hr = json.loads(text)
        return hr
    except json.JSONDecodeError:
        pass

    # 尝试提取 JSON 代码块
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个 { ... }
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法解析 AI 返回的 JSON: {text[:200]}...")


def fill_defaults(hr: dict) -> dict:
    """用默认值补全 human_review 缺失字段。"""
    default = copy.deepcopy(REVIEW_CONFIG["human_review_default"])
    default.update(hr)
    return default


def validate_human_review(hr: dict, pid: str) -> list:
    """基本校验，返回警告列表。"""
    warnings = []
    # problem_valid / final_accept 类型
    for field in ("problem_valid", "final_accept"):
        val = hr.get(field)
        if val is not None and not isinstance(val, bool):
            warnings.append(f"{pid}: {field} 应为 bool/null，实际 {type(val).__name__}，已置 null")
            hr[field] = None
    # wrong_model_names
    valid_names = REVIEW_CONFIG["valid_solver_names"]
    wmn = hr.get("wrong_model_names", [])
    if not isinstance(wmn, list):
        warnings.append(f"{pid}: wrong_model_names 应为 list，已重置为 []")
        hr["wrong_model_names"] = []
    else:
        bad = [n for n in wmn if n not in valid_names]
        if bad:
            warnings.append(f"{pid}: wrong_model_names 含无效值 {bad}，已过滤")
            hr["wrong_model_names"] = [n for n in wmn if n in valid_names]
    # reviewer_group
    if hr.get("reviewer_group") != REVIEW_CONFIG["reviewer_group"]:
        warnings.append(f"{pid}: reviewer_group 修正为 {REVIEW_CONFIG['reviewer_group']}")
        hr["reviewer_group"] = REVIEW_CONFIG["reviewer_group"]
    # corrected_execution_result 始终 null
    if hr.get("corrected_execution_result") is not None:
        warnings.append(f"{pid}: corrected_execution_result 应为 null，已重置")
        hr["corrected_execution_result"] = None
    return warnings


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="数据校对主入口：提取 + 按一致性分类 + AI 填充 human_review"
    )
    parser.add_argument("--start", type=int, default=1, help="起始行号（闭区间，1-based，默认 1）")
    parser.add_argument("--end", type=int, default=None, help="结束行号（闭区间，默认最后一行）")
    parser.add_argument("--no-ai", action="store_true", help="只分类提取，不调用 AI")
    args = parser.parse_args()

    input_path = PATHS["input_jsonl"]
    work_dir = PATHS["work_dir"]
    os.makedirs(work_dir, exist_ok=True)

    # ── 读取 JSONL ──
    if not os.path.exists(input_path):
        print(f"❌ 找不到文件: {input_path}")
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    total = len(all_lines)
    start_idx = args.start - 1
    end_idx = min((args.end if args.end else total) - 1, total - 1)

    if start_idx < 0 or start_idx >= total or start_idx > end_idx:
        print(f"❌ 区间无效: start={args.start}, end={args.end or total}, total={total}")
        sys.exit(1)

    count = end_idx - start_idx + 1
    print(f"📂 源文件: {input_path}")
    print(f"📊 总行数: {total}")
    print(f"📝 处理范围: 第 {args.start}-{end_idx + 1} 行（共 {count} 条）")
    print(f"🤖 AI: {'禁用' if args.no_ai else '启用'}")
    print()

    # ── 解析 + 分类 ──
    buckets = {"agree_all": [], "agree_two": [], "disagree_all": [], "missing": []}

    parse_fail = 0
    for i in range(start_idx, end_idx + 1):
        line = all_lines[i]
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            print(f"⚠️  第 {i + 1} 行 JSON 解析失败: {e}")
            parse_fail += 1
            continue

        pid = rec["problem_id"]
        cat = classify_consistency(rec)

        # 只保留 5 个必要字段
        slim = {
            "problem_id": pid,
            "question": rec["question"],
            "auto_reject": rec["auto_reject"],
            "review_guidance": rec["review_guidance"],
            "model_responses": rec["model_responses"],
            "human_review": copy.deepcopy(REVIEW_CONFIG["human_review_default"]),
            "_category": cat,
            "_line_index": i,
        }
        buckets[cat].append(slim)

    # ── 打印分类摘要 ──
    print("━" * 50)
    print("📊 分类统计:")
    for cat in ["agree_all", "agree_two", "disagree_all", "missing"]:
        label = CONSISTENCY_LABELS[cat]
        n = len(buckets[cat])
        print(f"   {cat:15s} ({label})：{n} 条")
    total_ok = sum(len(b) for b in buckets.values())
    print(f"   {'合计':15s}   {total_ok} 条（解析失败 {parse_fail} 条）")
    print("━" * 50)

    # ── AI 填充 ──
    if not args.no_ai:
        ai_total = 0
        ai_ok = 0
        ai_fail = 0
        warnings_all = []

        for cat in ["agree_all", "agree_two", "disagree_all", "missing"]:
            recs = buckets[cat]
            if not recs:
                continue

            prompt = load_prompt(cat)
            label = CONSISTENCY_LABELS[cat]
            print(f"\n🤖 [{label}] 共 {len(recs)} 条，开始 AI 审阅...")

            for idx, rec in enumerate(recs):
                pid = rec["problem_id"]
                ai_total += 1
                status_mark = f"[{idx + 1}/{len(recs)}]"
                try:
                    user_msg = build_user_message(rec, cat)
                    resp = call_ai(prompt, user_msg)
                    hr = parse_ai_response(resp, pid)
                    hr = fill_defaults(hr)
                    warns = validate_human_review(hr, pid)
                    rec["human_review"] = hr
                    warnings_all.extend(warns)
                    ai_ok += 1
                    marker = "⚠️ " if warns else ""
                    print(f"   {marker}{status_mark} {pid} ✅")
                except Exception as e:
                    ai_fail += 1
                    print(f"   {status_mark} {pid} ❌ {e}")

        print(f"\n🤖 AI 审阅完成: {ai_ok}/{ai_total} 成功, {ai_fail} 失败")
        if warnings_all:
            print(f"⚠️  {len(warnings_all)} 个字段警告（已自动修正）:")
            for w in warnings_all[:10]:
                print(f"   - {w}")
            if len(warnings_all) > 10:
                print(f"   ... 还有 {len(warnings_all) - 10} 条")

    # ── 写入文件 ──
    print()
    for cat in ["agree_all", "agree_two", "disagree_all", "missing"]:
        recs = buckets[cat]
        if not recs:
            continue
        filepath = os.path.join(work_dir, f"{cat}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(recs, f, indent=2, ensure_ascii=False)
        size_kb = os.path.getsize(filepath) / 1024
        label = CONSISTENCY_LABELS[cat]
        print(f"📄 work/{cat}.json  ({label})：{len(recs)} 条，{size_kb:.1f} KB")

    print()
    print("✅ 完成！")
    if args.no_ai:
        print("   未调用 AI（--no-ai），human_review 为空模板。")
        print("   人工填写后运行 writeback.py 写回。")
    else:
        print("   AI 已填充 human_review 初稿，请人工复核后运行 writeback.py 写回。")
    print(f"   → python3 scripts/writeback.py --start {args.start} --end {end_idx + 1}")


if __name__ == "__main__":
    main()
