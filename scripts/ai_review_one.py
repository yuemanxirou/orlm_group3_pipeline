#!/usr/bin/env python3
"""
ai_review_one.py —— 对单个 .md 审阅文件调 AI 填充 human_review

用法:
    python3 scripts/ai_review_one.py --input work/agree_two/v_xxx.md
    python3 scripts/ai_review_one.py --batch work/agree_two
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

# ── 原始数据按需索引 ──
_original_index = None

def _load_index():
    global _original_index
    if _original_index is not None:
        return _original_index
    _original_index = {}
    p = PATHS["input_jsonl"]
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    _original_index[r["problem_id"]] = r
                except json.JSONDecodeError:
                    pass
    return _original_index


def get_full(pid: str) -> dict:
    idx = _load_index()
    r = idx.get(pid, {})
    return {
        "question": r.get("question", {}),
        "auto_reject": r.get("auto_reject", {}),
        "review_guidance": r.get("review_guidance", {}),
        "model_responses": r.get("model_responses", []),
    }


# ── .md 解析 ──

def parse_frontmatter(content: str) -> dict:
    m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            v = v.strip()
            if v.isdigit():
                v = int(v)
            fm[k.strip()] = v
    return fm


def parse_review_json(content: str) -> dict:
    m = re.search(r"```review_json\s*\n(.*?)\n```", content, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return {}


def write_all_sections(content: str, hr: dict) -> str:
    """将 human_review 的三个部分写入 .md。"""
    reasoning = hr.pop("corrected_reasoning_trace", "")
    code = hr.pop("corrected_code", "")

    # 替换 corrected_reasoning_trace 段落
    content = re.sub(
        r"(## corrected_reasoning_trace\s*\n)\n?.*?(?=\n## corrected_code|\Z)",
        rf"\1\n{re.escape(reasoning)}" if reasoning else r"\1\n*(无)*",
        content, count=1, flags=re.DOTALL)

    # 替换 corrected_code 段落
    if code:
        code_block = "```python\n" + code + "\n```"
    else:
        code_block = "```python\n# (无)\n```"
    content = re.sub(
        r"## corrected_code\s*\n\n?```python\n.*?\n```",
        "## corrected_code\n\n" + code_block,
        content, count=1, flags=re.DOTALL)

    # 替换 review_json 块
    short_hr = {k: v for k, v in hr.items() if k not in ("corrected_reasoning_trace", "corrected_code")}
    new_block = "```review_json\n" + json.dumps(short_hr, indent=2, ensure_ascii=False) + "\n```"
    content = re.sub(r"```review_json\s*\n.*?\n```", new_block, content, count=1, flags=re.DOTALL)

    return content


# ── Prompt ──

def load_prompt(category: str) -> str:
    fn = CONSISTENCY_PROMPTS.get(category, "")
    if fn:
        pp = os.path.join(PATHS["prompts_dir"], fn)
        if os.path.exists(pp):
            with open(pp, "r", encoding="utf-8") as f:
                return f.read()
    return ""


def build_ai_message(full: dict, category: str) -> str:
    q = full["question"]; ar = full["auto_reject"]; rg = full["review_guidance"]
    c = ar.get("consensus", {})
    parts = [f"【{CONSISTENCY_LABELS.get(category, category)}】", ""]
    parts.append("## 题目\n" + q.get("problem", "") + "\n")
    parts.append("## 求解共识")
    obj = c.get("objectives", {}); st = c.get("statuses", {})
    for m in REVIEW_CONFIG["valid_solver_names"]:
        parts.append(f"- {m}: status={st.get(m, 'N/A')}, objective={obj.get(m, 'N/A')}")
    parts.append(f"- 拒绝原因: {'; '.join(ar.get('reject_reasons', []))}\n")
    parts.append("## 审阅指导\n" + rg.get("core_instruction", ""))
    for f in rg.get("focus", []):
        parts.append(f"- {f}")
    parts.append("\n## 模型求解轨迹")
    for mr in full["model_responses"]:
        parts.append(f"### {mr['solver_name']}\n" + mr.get("cot_chain", "") + "\n")
    return "\n".join(parts)


# ── AI ──

def call_ai(sp: str, um: str) -> str:
    payload = {"model": API_CONFIG["model"], "messages": [
        {"role": "system", "content": sp}, {"role": "user", "content": um}],
        "max_tokens": API_CONFIG["max_tokens"], "temperature": API_CONFIG["temperature"]}
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {API_CONFIG['api_key']}"}
    u = f"{API_CONFIG['base_url']}/chat/completions"
    for attempt in range(API_CONFIG["max_retries"]):
        try:
            req = urllib.request.Request(u, data=json.dumps(payload).encode("utf-8"), headers=h, method="POST")
            with urllib.request.urlopen(req, timeout=API_CONFIG["timeout_sec"]) as r:
                return json.loads(r.read().decode("utf-8"))["choices"][0]["message"]["content"]
        except Exception as e:
            if attempt == API_CONFIG["max_retries"] - 1:
                raise RuntimeError(e)
            time.sleep(2 ** attempt)
    raise RuntimeError("retries exhausted")


def parse_ai_json(text: str) -> dict:
    text = text.strip()
    try: return json.loads(text)
    except json.JSONDecodeError: pass
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(1))
        except json.JSONDecodeError: pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try: return json.loads(m.group(0))
        except json.JSONDecodeError: pass
    raise ValueError(f"无法解析: {text[:200]}...")


def sanitize(hr: dict) -> list:
    w = []
    d = copy.deepcopy(REVIEW_CONFIG["human_review_default"])
    for k in d:
        if k not in hr: hr[k] = d[k]
    for f in ("problem_valid", "final_accept"):
        if hr[f] is not None and not isinstance(hr[f], bool):
            hr[f] = None; w.append(f)
    vn = REVIEW_CONFIG["valid_solver_names"]
    if not isinstance(hr.get("wrong_model_names"), list):
        hr["wrong_model_names"] = []
    else:
        hr["wrong_model_names"] = [n for n in hr["wrong_model_names"] if n in vn]
    if hr.get("reviewer_group") != REVIEW_CONFIG["reviewer_group"]:
        hr["reviewer_group"] = REVIEW_CONFIG["reviewer_group"]
    if hr.get("corrected_execution_result") is not None:
        hr["corrected_execution_result"] = None
    return w


# ── 处理 ──

def get_category(filepath: str) -> str:
    for p in filepath.replace("\\", "/").split("/"):
        if p in CONSISTENCY_LABELS:
            return p
    return "agree_two"


def process_one(filepath: str) -> tuple:
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    fm = parse_frontmatter(content)
    pid = fm.get("problem_id") or os.path.basename(filepath).replace(".md", "")
    cat = fm.get("category") or get_category(filepath)

    full = get_full(pid)
    if not full.get("model_responses"):
        return False, [f"无法从原始数据获取 {pid}"]

    prompt = load_prompt(cat)
    if not prompt:
        return False, [f"无 prompt for {cat}"]

    resp = call_ai(prompt, build_ai_message(full, cat))
    hr = parse_ai_json(resp)
    sanitize(hr)
    content = write_all_sections(content, hr)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return True, []


def main():
    parser = argparse.ArgumentParser(description="对 .md 审阅文件调 AI")
    parser.add_argument("--input", default=None)
    parser.add_argument("--batch", default=None)
    args = parser.parse_args()

    if args.batch:
        d = args.batch
        if not os.path.isdir(d):
            print(f"❌ 目录不存在: {d}"); sys.exit(1)
        files = sorted([os.path.join(d, f) for f in os.listdir(d) if f.endswith(".md")])
        if not files:
            print(f"⚠️  {d} 下无 .md"); sys.exit(0)
        print(f"📂 {d} — {len(files)} 个文件")
        ok = fail = 0
        for fp in files:
            pid = os.path.basename(fp).replace(".md", "")
            try:
                process_one(fp); ok += 1
                print(f"   [{ok + fail}/{len(files)}] {pid} ✅")
            except Exception as e:
                fail += 1; print(f"   [{ok + fail}/{len(files)}] {pid} ❌ {e}")
        print(f"\n🤖 {ok}/{len(files)} 成功, {fail} 失败")
    elif args.input:
        if not os.path.exists(args.input):
            print(f"❌ 不存在: {args.input}"); sys.exit(1)
        pid = os.path.basename(args.input).replace(".md", "")
        process_one(args.input)
        print(f"✅ {pid} 已更新")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
