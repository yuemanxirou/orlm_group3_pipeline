#!/usr/bin/env python3
"""
fix_infeasible_items.py —— 自动检测假设话术 & 补全人工编辑的 human_review 字段

模式一 (--detect)：扫描所有 .md，检测模型求解思路中的假设话术 → AI 生成 notes → 标记 false → 移到 work/false/
模式二 (--fix-user-edited)：补全用户手动编辑的 19 个文件的所有 human_review 字段

用法:
    # 预览
    python3 scripts/fix_infeasible_items.py --detect --dry-run
    python3 scripts/fix_infeasible_items.py --list-only

    # 自动检测 + AI notes + 移动
    python3 scripts/fix_infeasible_items.py --detect

    # 补全用户 19 个文件
    python3 scripts/fix_infeasible_items.py --fix-user-edited

    # 一起跑
    python3 scripts/fix_infeasible_items.py --detect --fix-user-edited
"""

import argparse
import concurrent.futures
import copy
import json
import os
import re
import sys
import threading
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import API_CONFIG, PATHS, REVIEW_CONFIG, CONSISTENCY_LABELS

# ============================================================
# 假设话术正则
# ============================================================
ASSUMPTION_PATTERNS = [
    r"没有单独给出价值",
    r"在这里做出假设",
    r"故在这里做出假设",
    r"由于问题没有给出",
    r"题面中未给出",
    r"题面未给出",
    r"由于题目没有给出",
    r"由于题目只给出",
    r"题目只给出了",
    r"由于题面未给出",
    r"题面没有提供",
    r"题目没有提供",
    r"题面.*不完整",
    r"题目.*不完整",
    r"缺少.*关键.*数据",
    r"缺少.*信息",
]

# 用户手动编辑的 19 个文件
USER_EDITED_FILES = [
    ("1368", "work/agree_two/v_2fc8dec28faed3.md", False),
    ("1246", "work/agree_two/v_02dfbabb9c3fdd.md", True),
    ("1451", "work/agree_two/v_2dd52a9baf7a60.md", True),
    ("1290", "work/agree_two/v_2daa89dbc2e063.md", False),
    ("1151", "work/agree_two/v_2d4290a87b5d9e.md", False),
    ("1441", "work/agree_two/v_2cab6c63e8029d.md", False),
    ("1426", "work/agree_two/v_2c483e204b3db7.md", False),
    ("1066", "work/agree_two/v_2b23e7d2f97a24.md", True),
    ("1562", "work/agree_two/v_1fde3549e1ad74.md", True),
    ("1182", "work/agree_two/v_1eed3a71a9dab1.md", False),
    ("1423", "work/agree_two/v_1c5987f68a2ed3.md", False),
    ("1547", "work/agree_two/v_1bcd94bcd8a5ce.md", True),
    ("1164", "work/agree_two/v_0e64bfa0dc14b4.md", True),
    ("1378", "work/agree_two/v_0d125924fa604a.md", True),
    ("1345", "work/agree_two/v_0c19d83c6ba7bd.md", False),
    ("1190", "work/agree_two/v_0bf1dd2b7e4712.md", True),
    ("1044", "work/agree_two/v_0a30363fdf4966.md", False),
    ("1502", "work/agree_two/v_0a51e877032b83.md", True),
    ("1405", "work/agree_two/v_0a3eadbbe3421e.md", False),
]


# ============================================================
# 解析工具
# ============================================================

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


def extract_code(content: str) -> str:
    """从 ## corrected_code 段落提取 Python 代码。"""
    text = parse_section(content, "corrected_code")
    if text.startswith("# 在此粘贴") or text.startswith("# 题目不可行"):
        return ""
    # 如果已经是提取过的代码（没有 code fence）
    if text and not text.startswith("```"):
        return text
    return text


def parse_review_json(content: str) -> dict:
    """提取 review_json 代码块。"""
    m = re.search(r"```review_json\s*\n(.*?)\n```", content, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


# ============================================================
# 假设话术检测
# ============================================================

def extract_model_reasoning(content: str) -> list:
    """
    提取 ## 模型求解轨迹 中每个模型的 【求解思路】 内容。
    返回 [(model_name, reasoning_text), ...]
    """
    # 定位模型求解轨迹段落到 --- 或文件末尾
    m = re.search(r"## 模型求解轨迹\s*\n+(.*?)(?=\n---\n|\n## human_review|\Z)", content, re.DOTALL)
    if not m:
        return []
    trajectory_section = m.group(1)

    # 按 ### 模型名 拆分
    blocks = re.split(r"\n### (.+?)\s*\n", trajectory_section)
    results = []
    # blocks[0] 是第一个 ### 之前的内容（空或说明文字）
    for i in range(1, len(blocks), 2):
        model_name = blocks[i].strip()
        model_content = blocks[i + 1] if i + 1 < len(blocks) else ""
        # 提取 【求解思路】 内容（在下一个 【COPT代码】 之前）
        thinking_m = re.search(r"【求解思路】\s*\n(.*?)(?=\n【COPT代码】|\Z)", model_content, re.DOTALL)
        if thinking_m:
            reasoning = thinking_m.group(1).strip()
            results.append((model_name, reasoning))
    return results


def has_assumption_language(content: str, wrong_models: list) -> tuple:
    """
    检查未被标记为 wrong 的模型的求解思路是否包含假设话术。
    返回 (detected: bool, snippets: list[str])
    """
    model_reasonings = extract_model_reasoning(content)
    if not model_reasonings:
        return False, []

    wrong_set = set(wrong_models) if wrong_models else set()
    snippets = []

    for model_name, reasoning in model_reasonings:
        # 跳过已标记为错误的模型
        if model_name in wrong_set:
            continue
        for pattern in ASSUMPTION_PATTERNS:
            if re.search(pattern, reasoning):
                # 提取匹配周围的上下文
                ctx_m = re.search(r".{0,80}" + pattern + r".{0,120}", reasoning)
                ctx = ctx_m.group(0) if ctx_m else reasoning[:300]
                snippets.append(f"{model_name}: {ctx}")
                break  # 一个模型只记一次

    return len(snippets) > 0, snippets


def get_problem_text(content: str) -> str:
    """从 .md 中提取题面。"""
    text = parse_section(content, "题目")
    if len(text) > 800:
        text = text[:800] + "..."
    return text


# ============================================================
# AI 调用
# ============================================================

INFEASIBLE_SYSTEM_PROMPT = """你是一位运筹优化领域的审阅专家。你会收到一道题目的题干和模型求解思路中发现的"假设语言"片段。这些片段表明大模型在求解时发现题目缺少关键信息，不得不自行假设。

请根据这些片段，用一句简洁的中文说明这道题目为什么不可行（infeasible）。你的回复必须是严格的 JSON 格式：

{"notes": "你的中文说明"}

要求：
1. notes 必须是完整的中文句子，以句号结尾
2. 说明要具体指出缺少什么关键数据，而不是笼统地说"信息不足"
3. 控制在 30-80 字以内
4. 使用"由于题面/题目没有给出/只给出了..."的句式
5. 不要使用 markdown 格式
6. 只输出 JSON，不要输出其他内容"""


def call_ai(system_prompt: str, user_message: str) -> str:
    """调用 AI API。复用 run_ai_review.py 的模式。"""
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
    """解析 AI 返回的 JSON。复用 run_ai_review.py 的模式。"""
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


def generate_infeasible_notes(snippets: list, problem_text: str) -> str:
    """调用 AI 生成 infeasible 原因的 notes。"""
    snippets_text = "\n".join(f"- {s}" for s in snippets[:5])
    user_message = f"""## 题目
{problem_text}

## 模型求解思路中发现的假设语言
{snippets_text}

请生成 notes。"""
    try:
        resp = call_ai(INFEASIBLE_SYSTEM_PROMPT, user_message)
        result = parse_ai_json(resp)
        notes = result.get("notes", "")
        if notes:
            return notes
    except Exception as e:
        print(f"   ⚠️ AI 生成 notes 失败: {e}")
    # fallback
    first = snippets[0] if snippets else "题面信息不完整"
    short = first.split(": ", 1)[-1] if ": " in first else first
    return f"由于{short[:100]}，题目信息不完整，无法正确建模。"


# ============================================================
# .md 文件修复
# ============================================================

INF_FEASIBLE_DEFAULT = {
    "reviewer_group": "group_3",
    "problem_valid": False,
    "wrong_model_names": [],
    "corrected_reasoning_trace": "",
    "corrected_code": "",
    "corrected_execution_result": None,
    "notes": "",
    "final_accept": False,
}


def build_infeasible_hr(user_hr: dict, notes: str) -> dict:
    """构建完整的 infeasible human_review dict。优先保留用户已填字段。"""
    hr = copy.deepcopy(INF_FEASIBLE_DEFAULT)
    # 合并用户已有的字段
    for k, v in user_hr.items():
        if v is not None and v != "" and v != [] and v != {}:
            hr[k] = v
    # notes: AI 生成 or 用户已有
    if not hr.get("notes"):
        hr["notes"] = notes
    # 强制 infeasible 字段
    hr["problem_valid"] = False
    hr["final_accept"] = False
    hr["corrected_reasoning_trace"] = ""
    hr["corrected_code"] = ""
    hr["corrected_execution_result"] = None
    hr["reviewer_group"] = REVIEW_CONFIG["reviewer_group"]
    hr["wrong_model_names"] = hr.get("wrong_model_names", [])
    if not isinstance(hr["wrong_model_names"], list):
        hr["wrong_model_names"] = []
    return hr


def clear_section(content: str, heading: str, placeholder: str) -> str:
    """清空指定 ## heading 段落的内容。"""
    pattern = rf"(## {heading}\s*\n)\n?(.*?)(?=\n## |\n---|\Z)"
    m = re.search(pattern, content, re.DOTALL)
    if not m:
        return content
    new_content = m.group(1) + "\n" + placeholder + "\n"
    return content[:m.start()] + new_content + content[m.end():]


def write_review_json_block(content: str, hr: dict) -> str:
    """将完整的 human_review（含全部 8 个字段）写入 .md 的 review_json 代码块。"""
    new_json = json.dumps(hr, indent=2, ensure_ascii=False)
    new_block = f"```review_json\n{new_json}\n```"

    old_m = re.search(r"```review_json\s*\n.*?\n```", content, re.DOTALL)
    if old_m:
        return content[:old_m.start()] + new_block + content[old_m.end():]
    return content


def fix_md_as_infeasible(filepath: str, notes: str, dry_run: bool = False) -> bool:
    """将 .md 文件修复为 infeasible 格式。"""
    pid = os.path.basename(filepath).replace(".md", "")
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    user_hr = parse_review_json(content)
    hr = build_infeasible_hr(user_hr, notes)

    if dry_run:
        print(f"   [DRY RUN] 将修复: {filepath}")
        return True

    # 清空独立段落
    content = clear_section(content, "corrected_reasoning_trace", "*(题目 infeasible，无需填写)*")
    content = clear_section(content, "corrected_code", "*(题目 infeasible，无需填写)*")
    content = clear_section(content, "corrected_execution_result", "*(题目 infeasible，无需运行)*")

    # 更新 review_json
    content = write_review_json_block(content, hr)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"   ✅ 已修复: {filepath}")
    return True


def move_to_false(filepath: str, dry_run: bool = False) -> str:
    """将文件移到 work/false/。"""
    false_dir = os.path.join(PATHS["work_dir"], "false")
    basename = os.path.basename(filepath)
    dest = os.path.join(false_dir, basename)

    if dry_run:
        print(f"   [DRY RUN] 将移动: {filepath} → {dest}")
        return dest

    os.makedirs(false_dir, exist_ok=True)
    os.rename(filepath, dest)
    print(f"   📦 已移动: {basename} → work/false/")
    return dest


# ============================================================
# 模式二：补全用户编辑的 19 个文件
# ============================================================

def fix_user_edited_file(filepath: str, dry_run: bool = False) -> str:
    """
    补全用户手动编辑的文件的所有 human_review 字段。
    返回 "false" 或 "true"（文件最终的 problem_valid 状态）。
    """
    pid = os.path.basename(filepath).replace(".md", "")
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    user_hr = parse_review_json(content)
    pv = user_hr.get("problem_valid")

    if pv is False:
        # --- INF-Feasible: 补全 + 清空 + 移动 ---
        # 构建完整 hr
        hr = {
            "reviewer_group": REVIEW_CONFIG["reviewer_group"],
            "problem_valid": False,
            "wrong_model_names": user_hr.get("wrong_model_names", []),
            "corrected_reasoning_trace": "",
            "corrected_code": "",
            "corrected_execution_result": None,
            "notes": user_hr.get("notes", ""),
            "final_accept": False,
        }
        if not isinstance(hr["wrong_model_names"], list):
            hr["wrong_model_names"] = []

        if dry_run:
            print(f"   [DRY RUN] 修复 false: {filepath}")
        else:
            # 清空独立段落
            content = clear_section(content, "corrected_reasoning_trace",
                                    "*(题目 infeasible，无需填写)*")
            content = clear_section(content, "corrected_code",
                                    "*(题目 infeasible，无需填写)*")
            content = clear_section(content, "corrected_execution_result",
                                    "*(题目 infeasible，无需运行)*")
            content = write_review_json_block(content, hr)
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.write(content)
            print(f"   ✅ 已修复 false: {filepath}")

        # 移动到 work/false/
        move_to_false(filepath, dry_run)
        return "false"

    elif pv is True:
        # --- FEASIBLE: 从独立段落复制到 review_json ---
        reasoning = parse_section(content, "corrected_reasoning_trace")
        # 过滤占位文本
        if reasoning and reasoning.startswith("*("):
            reasoning = ""
        code = extract_code(content)
        if code and code.startswith("# 在此粘贴"):
            code = ""

        hr = {
            "reviewer_group": REVIEW_CONFIG["reviewer_group"],
            "problem_valid": True,
            "wrong_model_names": user_hr.get("wrong_model_names", []),
            "corrected_reasoning_trace": reasoning,
            "corrected_code": code,
            "corrected_execution_result": None,
            "notes": user_hr.get("notes", ""),
            "final_accept": True,
        }
        if not isinstance(hr["wrong_model_names"], list):
            hr["wrong_model_names"] = []

        if dry_run:
            print(f"   [DRY RUN] 修复 true: {filepath} (reasoning={len(reasoning)}字, code={len(code)}字)")
        else:
            content = write_review_json_block(content, hr)
            with open(filepath, "w", encoding="utf-8") as fh:
                fh.write(content)
            print(f"   ✅ 已修复 true: {filepath} (reasoning={len(reasoning)}字, code={len(code)}字)")

        return "true"

    else:
        print(f"   ⚠️ [{pid}] problem_valid 为 None，跳过（未审阅）")
        return "none"


# ============================================================
# 扫描与检测
# ============================================================

def scan_for_infeasible(dirs: list = None) -> list:
    """
    扫描 work/ 下所有 .md，检测假设话术。
    返回 [(filepath, snippets), ...]
    """
    if dirs is None:
        work_dir = PATHS["work_dir"]
        dirs = []
        for cat in ["agree_all", "agree_two", "disagree_all", "missing"]:
            d = os.path.join(work_dir, cat)
            if os.path.isdir(d):
                dirs.append(d)

    results = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".md") or fname.startswith("."):
                continue
            filepath = os.path.join(d, fname)
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            user_hr = parse_review_json(content)
            wrong_models = user_hr.get("wrong_model_names", [])
            if not isinstance(wrong_models, list):
                wrong_models = []

            detected, snippets = has_assumption_language(content, wrong_models)
            if detected:
                results.append((filepath, snippets))

    return results


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="自动检测假设话术 & 补全人工编辑的 human_review 字段"
    )
    parser.add_argument("--detect", action="store_true", help="模式一：扫描检测假设话术，标记 false 并移动")
    parser.add_argument("--fix-user-edited", action="store_true", help="模式二：补全用户手动编辑的 19 个文件")
    parser.add_argument("--list-only", action="store_true", help="仅列出检测到的假设话术文件，不做修改")
    parser.add_argument("--dry-run", action="store_true", help="预览模式，不实际修改文件")
    parser.add_argument("--no-ai", action="store_true", help="跳过 AI，使用模板生成 notes")
    parser.add_argument("--workers", type=int, default=5, help="并发 AI 调用数（默认 5）")
    args = parser.parse_args()

    if not args.detect and not args.fix_user_edited and not args.list_only:
        parser.print_help()
        return

    base_dir = PATHS["work_dir"]

    # ============================================================
    # 模式一：检测假设话术
    # ============================================================
    if args.detect or args.list_only:
        print("━" * 60)
        print("🔍 模式一：扫描假设话术")
        print("━" * 60)
        print()

        dirs = []
        for cat in ["agree_all", "agree_two", "disagree_all", "missing"]:
            d = os.path.join(base_dir, cat)
            if os.path.isdir(d):
                dirs.append(d)
        print(f"📂 扫描目录: {', '.join(os.path.basename(d) for d in dirs)}")

        infeasible_files = scan_for_infeasible(dirs)
        print(f"\n🔍 检测到 {len(infeasible_files)} 个文件包含假设话术")

        if args.list_only:
            print()
            for filepath, snippets in infeasible_files:
                pid = os.path.basename(filepath).replace(".md", "")
                print(f"  {filepath}")
                for s in snippets[:3]:
                    print(f"    → {s[:120]}")
            print(f"\n共 {len(infeasible_files)} 个文件")
            return

        if not infeasible_files:
            print("✅ 未发现假设话术文件")
            return

        # 过滤掉用户已编辑的文件（避免重复处理）
        user_file_set = {p for _, p, _ in USER_EDITED_FILES}
        to_process = [(fp, s) for fp, s in infeasible_files if fp not in user_file_set]
        already_user = len(infeasible_files) - len(to_process)
        if already_user > 0:
            print(f"   其中 {already_user} 个在用户 19 个列表中，跳过（由 --fix-user-edited 处理）")
        print(f"   待处理: {len(to_process)} 个\n")

        if not to_process:
            print("✅ 所有假设话术文件已在用户列表中")
            return

        if args.dry_run:
            print("🔍 DRY RUN 模式 — 仅预览，不修改\n")
            for filepath, snippets in to_process:
                pid = os.path.basename(filepath).replace(".md", "")
                print(f"  [{pid}]")
                for s in snippets[:2]:
                    print(f"    → {s[:150]}")
                print()
            print(f"共 {len(to_process)} 个文件将被修复并移至 work/false/")
            return

        # 生成 notes（并行 AI 调用）
        print("🤖 生成 infeasible notes...")
        print_lock = threading.Lock()
        ai_ok = 0
        ai_fail = 0
        notes_map = {}

        if args.no_ai:
            for filepath, snippets in to_process:
                problem_text = get_problem_text(
                    open(filepath, "r", encoding="utf-8").read())
                first = snippets[0].split(": ", 1)[-1] if ": " in snippets[0] else snippets[0]
                notes_map[filepath] = f"由于{first[:120]}，题目信息不完整，无法正确建模。"
        else:
            def gen_notes(task):
                filepath, snippets = task
                nonlocal ai_ok, ai_fail
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        content = f.read()
                    problem_text = get_problem_text(content)
                    notes = generate_infeasible_notes(snippets, problem_text)
                    with print_lock:
                        ai_ok += 1
                        pid = os.path.basename(filepath).replace(".md", "")
                        print(f"   [{ai_ok + ai_fail}/{len(to_process)}] {pid} ✅ {notes[:60]}...")
                    return (filepath, notes)
                except Exception as e:
                    with print_lock:
                        ai_fail += 1
                        pid = os.path.basename(filepath).replace(".md", "")
                        print(f"   [{ai_ok + ai_fail}/{len(to_process)}] {pid} ❌ {e}")
                    # fallback
                    first = snippets[0].split(": ", 1)[-1] if ": " in snippets[0] else snippets[0]
                    return (filepath, f"由于{first[:120]}，题目信息不完整，无法正确建模。")

            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = [executor.submit(gen_notes, (fp, s)) for fp, s in to_process]
                for future in concurrent.futures.as_completed(futures):
                    fp, notes = future.result()
                    notes_map[fp] = notes

            print(f"\n🤖 AI notes: {ai_ok}/{len(to_process)} 成功, {ai_fail} 失败")

        # 修复 + 移动
        print("\n📝 修复 .md 文件并移至 work/false/...")
        fixed = 0
        moved = 0
        for filepath, snippets in to_process:
            notes = notes_map.get(filepath, "题目信息不完整，无法正确建模。")
            if fix_md_as_infeasible(filepath, notes, dry_run=False):
                fixed += 1
            if move_to_false(filepath, dry_run=False):
                moved += 1

        print(f"\n📊 完成: {fixed} 修复, {moved} 移动")

    # ============================================================
    # 模式二：补全用户编辑的 19 个文件
    # ============================================================
    if args.fix_user_edited:
        print("━" * 60)
        print("✍️  模式二：补全用户编辑的 19 个文件")
        print("━" * 60)
        print()

        false_count = 0
        true_count = 0
        none_count = 0

        for line_idx, filepath, _ in USER_EDITED_FILES:
            if not os.path.exists(filepath):
                print(f"   ⚠️ [{line_idx}] 文件不存在: {filepath}")
                continue

            result = fix_user_edited_file(filepath, args.dry_run)
            if result == "false":
                false_count += 1
            elif result == "true":
                true_count += 1
            else:
                none_count += 1

        print(f"\n📊 完成: {false_count} 个 infeasible（已移入 work/false/）, "
              f"{true_count} 个 feasible（已补全）, {none_count} 个跳过")

    # ============================================================
    # 汇总
    # ============================================================
    print()
    if not args.dry_run and not args.list_only:
        false_dir = os.path.join(base_dir, "false")
        if os.path.isdir(false_dir):
            count = len([f for f in os.listdir(false_dir) if f.endswith(".md")])
            print(f"📁 work/false/ 当前共 {count} 个文件")
    print("✅ 完成！")


if __name__ == "__main__":
    main()
