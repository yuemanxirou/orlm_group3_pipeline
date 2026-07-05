#!/usr/bin/env python3
"""
run_corrected_code.py —— 从 .md 审阅文件中提取 corrected_code，本地运行，填入 corrected_execution_result

用法:
    python3 scripts/run_corrected_code.py --input work/agree_two/v_xxx.md
    python3 scripts/run_corrected_code.py --batch
    python3 scripts/run_corrected_code.py --batch --category agree_two --start 1 --end 50
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time


def extract_code(content: str) -> str:
    """从 .md 的 ## corrected_code 段落提取 Python 代码。"""
    pattern = r"## corrected_code\s*\n\n?```(?:python)?\s*\n(.*?)\n```"
    m = re.search(pattern, content, re.DOTALL)
    if not m:
        return ""
    code = m.group(1).strip()
    # 过滤占位提示行
    if code.startswith("# 在此粘贴"):
        return ""
    return code


def extract_review_json(content: str) -> dict:
    """提取 review_json 代码块。"""
    m = re.search(r"```review_json\s*\n(.*?)\n```", content, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}


def run_python_code(code: str, timeout_sec: int = 30) -> dict:
    """
    在子进程中执行 Python 代码。
    返回 {"success": bool, "stdout": str, "stderr": str, "elapsed_ms": int}
    """
    result = {"success": False, "stdout": "", "stderr": "", "elapsed_ms": 0}
    start = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        result["stdout"] = proc.stdout.strip()
        result["stderr"] = proc.stderr.strip()
        result["success"] = proc.returncode == 0
        if proc.returncode != 0:
            result["stderr"] = f"[exit code {proc.returncode}]\n{result['stderr']}"
    except subprocess.TimeoutExpired:
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        result["stderr"] = f"⏱ 执行超时（>{timeout_sec}s）"
    except Exception as e:
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        result["stderr"] = f"❌ 执行异常: {type(e).__name__}: {e}"
    return result


def format_execution_result(run_result: dict) -> str:
    """将运行结果格式化为 readable string。"""
    parts = []
    parts.append(f"// exit: {'ok' if run_result['success'] else 'error'}  |  time: {run_result['elapsed_ms']}ms")
    if run_result["stdout"]:
        parts.append(run_result["stdout"])
    if run_result["stderr"]:
        parts.append(f"// stderr:\n{run_result['stderr']}")
    return "\n".join(parts).strip()


def update_md_file(filepath: str, execution_result: str) -> bool:
    """将 corrected_execution_result 写回 .md 文件。"""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    review_json = extract_review_json(content)
    old_result = review_json.get("corrected_execution_result", None)
    review_json["corrected_execution_result"] = execution_result

    new_json_str = json.dumps(review_json, indent=2, ensure_ascii=False)
    old_block = re.search(r"```review_json\s*\n.*?\n```", content, re.DOTALL)
    if not old_block:
        return False

    new_block = f"```review_json\n{new_json_str}\n```"
    new_content = content[:old_block.start()] + new_block + content[old_block.end():]

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)
    return True


def process_single(filepath: str, timeout_sec: int = 30, dry_run: bool = False) -> bool:
    """处理单个 .md 文件。返回是否成功。"""
    pid = os.path.basename(filepath).replace(".md", "")

    if not os.path.exists(filepath):
        print(f"❌ [{pid}] 文件不存在: {filepath}")
        return False

    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    code = extract_code(content)
    if not code:
        print(f"⚠️  [{pid}] 未找到 corrected_code（可能尚未填写）")
        return False

    review_json = extract_review_json(content)
    old_result = review_json.get("corrected_execution_result", None)
    if old_result and old_result is not None and str(old_result).strip():
        print(f"⚠️  [{pid}] corrected_execution_result 已有值，跳过（如需重跑请先清空）")
        return False

    print(f"▶ [{pid}] 运行 corrected_code ({len(code)} 字符)...", end=" ")

    if dry_run:
        print("[DRY RUN] 跳过执行")
        return False

    run_result = run_python_code(code, timeout_sec=timeout_sec)
    result_str = format_execution_result(run_result)

    status = "✅" if run_result["success"] else "⚠️"
    print(f"{status} {run_result['elapsed_ms']}ms")

    if run_result["stdout"]:
        print(f"   stdout: {run_result['stdout'][:120]}")
    if run_result["stderr"]:
        print(f"   stderr: {run_result['stderr'][:120]}")

    if update_md_file(filepath, result_str):
        print(f"   📝 已写入 corrected_execution_result")
    else:
        print(f"   ❌ 写入失败")
        return False

    return True


def main():
    parser = argparse.ArgumentParser(
        description="从 .md 审阅文件提取 corrected_code 并本地运行，填入 corrected_execution_result"
    )
    parser.add_argument("--input", default=None, help="单个 .md 文件路径")
    parser.add_argument("--batch", action="store_true", help="批量扫描 work/ 下所有 .md")
    parser.add_argument("--category", default=None, choices=["agree_all", "agree_two", "disagree_all", "missing"])
    parser.add_argument("--timeout", type=int, default=30, help="代码执行超时秒数（默认 30）")
    parser.add_argument("--dry-run", action="store_true", help="仅列出会执行的题目，不实际运行")
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    work_dir = os.path.join(base_dir, "work")

    if args.input:
        success = process_single(args.input, timeout_sec=args.timeout, dry_run=args.dry_run)
        sys.exit(0 if success else 1)

    if args.batch:
        if not os.path.isdir(work_dir):
            print(f"❌ work/ 目录不存在: {work_dir}")
            sys.exit(1)

        categories = [args.category] if args.category else ["agree_all", "agree_two", "disagree_all", "missing"]
        files = []
        for cat in categories:
            cat_dir = os.path.join(work_dir, cat)
            if not os.path.isdir(cat_dir):
                continue
            for fname in sorted(os.listdir(cat_dir)):
                if fname.endswith(".md") and not fname.startswith("."):
                    files.append(os.path.join(cat_dir, fname))

        if not files:
            print("⚠️  未发现 .md 文件")
            sys.exit(0)

        print(f"📂 扫描 work/ → {len(files)} 个 .md 文件")
        if args.dry_run:
            print("🔍 DRY RUN 模式\n")

        ok = skip = fail = 0
        for filepath in files:
            result = process_single(filepath, timeout_sec=args.timeout, dry_run=args.dry_run)
            if result:
                ok += 1
            else:
                skip += 1  # no code / already filled / dry run

        print(f"\n📊 完成: {ok} 成功, {skip} 跳过, {fail} 失败")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
