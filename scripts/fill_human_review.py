#!/usr/bin/env python3
"""
fill_human_review.py —— 对已有分类 JSON 文件独立调 AI 填充 human_review。

适用于：
    - run_ai_review.py 用了 --no-ai 后，单独跑 AI
    - 对 AI 初稿不满意，重跑某类数据

用法:
    python3 scripts/fill_human_review.py --input work/agree_two.json
    python3 scripts/fill_human_review.py --input work/agree_two.json --start 0 --count 3  # 只跑前3条
    python3 scripts/fill_human_review.py --input work/agree_two.json --dry-run             # 只打印
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


def load_prompt(category: str) -> str:
    filename = CONSISTENCY_PROMPTS.get(category)
    if not filename:
        return ""
    prompt_path = os.path.join(PATHS["prompts_dir"], filename)
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def build_user_message(rec: dict, category: str) -> str:
    """与 run_ai_review.py 保持一致的 user message 构建。"""
    q = rec["question"]
    ar = rec["auto_reject"]
    rg = rec["review_guidance"]
    consensus = ar.get("consensus", {})

    parts = []
    parts.append(f"【分类】{CONSISTENCY_LABELS.get(category, category)}")
    parts.append("")
    parts.append("## 题目")
    parts.append(q.get("problem", ""))
    parts.append("")

    parts.append("## 求解共识")
    objectives = consensus.get("objectives", {})
    statuses = consensus.get("statuses", {})
    for m in REVIEW_CONFIG["valid_solver_names"]:
        parts.append(f"- {m}: status={statuses.get(m, 'N/A')}, objective={objectives.get(m, 'N/A')}")
    parts.append(f"- 拒绝原因: {'; '.join(ar.get('reject_reasons', []))}")
    parts.append("")

    parts.append("## 审阅指导")
    parts.append(rg.get("core_instruction", ""))
    focus = rg.get("focus", [])
    if focus:
        parts.append("审查重点:")
        for f in focus:
            parts.append(f"- {f}")
    parts.append("")

    parts.append("## 模型求解轨迹")
    for mr in rec["model_responses"]:
        parts.append(f"### {mr['solver_name']}")
        parts.append(mr.get("cot_chain", ""))
        parts.append("")

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
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_CONFIG['api_key']}",
    }
    url = f"{API_CONFIG['base_url']}/chat/completions"

    last_error = None
    for attempt in range(API_CONFIG["max_retries"]):
        try:
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode("utf-8"),
                headers=headers, method="POST",
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
    raise ValueError(f"无法解析 AI 返回的 JSON: {text[:200]}...")


def fill_defaults(hr: dict) -> dict:
    default = copy.deepcopy(REVIEW_CONFIG["human_review_default"])
    default.update(hr)
    return default


def validate_human_review(hr: dict, pid: str) -> list:
    warnings = []
    for field in ("problem_valid", "final_accept"):
        val = hr.get(field)
        if val is not None and not isinstance(val, bool):
            warnings.append(f"{pid}: {field} 应为 bool/null，已置 null")
            hr[field] = None
    valid_names = REVIEW_CONFIG["valid_solver_names"]
    wmn = hr.get("wrong_model_names", [])
    if not isinstance(wmn, list):
        hr["wrong_model_names"] = []
        warnings.append(f"{pid}: wrong_model_names 已重置")
    else:
        bad = [n for n in wmn if n not in valid_names]
        if bad:
            hr["wrong_model_names"] = [n for n in wmn if n in valid_names]
            warnings.append(f"{pid}: wrong_model_names 过滤无效值 {bad}")
    if hr.get("reviewer_group") != REVIEW_CONFIG["reviewer_group"]:
        hr["reviewer_group"] = REVIEW_CONFIG["reviewer_group"]
    if hr.get("corrected_execution_result") is not None:
        hr["corrected_execution_result"] = None
    return warnings


def main():
    parser = argparse.ArgumentParser(description="独立对分类 JSON 调 AI 填充 human_review")
    parser.add_argument("--input", required=True, help="分类 JSON 文件路径")
    parser.add_argument("--start", type=int, default=0, help="从第几条开始（0-based，默认 0）")
    parser.add_argument("--count", type=int, default=None, help="处理条数（默认全部）")
    parser.add_argument("--dry-run", action="store_true", help="只打印不调 API")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"❌ 找不到文件: {args.input}")
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        records = json.load(f)

    total = len(records)
    start_idx = args.start
    end_idx = min(start_idx + args.count, total) if args.count else total

    print(f"📂 文件: {args.input}")
    print(f"📊 总记录: {total}")
    print(f"📝 处理范围: [{start_idx}, {end_idx - 1}]（{end_idx - start_idx} 条）")

    if args.dry_run:
        for i in range(start_idx, end_idx):
            rec = records[i]
            print(f"   [{i}] {rec['problem_id']}  |  {CONSISTENCY_LABELS.get(rec.get('_category', ''), '')}")
        print("🔍 DRY RUN 完成")
        return

    # 识别分类（从文件名）
    basename = os.path.basename(args.input)
    category = basename.rsplit(".", 1)[0]  # e.g. "agree_two"
    prompt = load_prompt(category)
    if not prompt:
        print(f"⚠️  未找到分类 '{category}' 的 prompt，将使用 agree_two 作为回退")
        prompt = load_prompt("agree_two")

    ok = 0
    fail = 0
    warnings_all = []

    for i in range(start_idx, end_idx):
        rec = records[i]
        pid = rec["problem_id"]
        cat = rec.get("_category", category)
        marker = f"[{i - start_idx + 1}/{end_idx - start_idx}]"

        try:
            user_msg = build_user_message(rec, cat)
            resp = call_ai(prompt, user_msg)
            hr = parse_ai_response(resp, pid)
            hr = fill_defaults(hr)
            warns = validate_human_review(hr, pid)
            rec["human_review"] = hr
            warnings_all.extend(warns)
            ok += 1
            flag = "⚠️ " if warns else ""
            print(f"   {flag}{marker} {pid} ✅")
        except Exception as e:
            fail += 1
            print(f"   {marker} {pid} ❌ {e}")

    # 写回
    out_path = args.input.replace(".json", "_reviewed.json")
    out_dir = os.path.dirname(args.input)
    reviewed_dir = os.path.join(out_dir, "reviewed")
    os.makedirs(reviewed_dir, exist_ok=True)
    out_path = os.path.join(reviewed_dir, basename.replace(".json", "_reviewed.json"))

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    print(f"\n🤖 AI 完成: {ok}/{end_idx - start_idx} 成功, {fail} 失败")
    if warnings_all:
        print(f"⚠️  {len(warnings_all)} 个字段警告（已自动修正）")
    print(f"📄 输出: {out_path}")


if __name__ == "__main__":
    main()
