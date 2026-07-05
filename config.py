"""
数据校对工具 —— 集中配置文件
所有脚本共享此配置，修改参数只需编辑此文件。
"""

import os

# 尝试从 .env 文件加载（本地开发），失败则用环境变量
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_ENV_PATH):
    with open(_ENV_PATH) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

# ============================================================
# API 配置
# ============================================================
API_CONFIG = {
    "base_url": "https://api.shubiaobiao.cn/v1",
    "api_key": os.environ.get("API_KEY", ""),
    "model": "gpt-5.4-mini",
    "max_tokens": 4096,
    "temperature": 0.0,
    "timeout_sec": 120,
    "max_retries": 3,
}

# ============================================================
# 项目路径（自动基于此文件所在目录）
# ============================================================
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PATHS = {
    "input_jsonl": os.path.join(_BASE_DIR, "group_3.jsonl"),
    "work_dir": os.path.join(_BASE_DIR, "work"),
    "output_dir": os.path.join(_BASE_DIR, "output"),
    "prompts_dir": os.path.join(_BASE_DIR, "prompts"),
}

# ============================================================
# 人工审查配置
# ============================================================
REVIEW_CONFIG = {
    "reviewer_group": "group_3",
    "valid_solver_names": ["gpt-5.5", "claude-opus-4-8", "deepseek-v4-pro"],
    "expected_line_count": 1623,
    "human_review_fields": [
        "reviewer_group", "problem_valid", "wrong_model_names",
        "corrected_reasoning_trace", "corrected_code",
        "corrected_execution_result", "notes", "final_accept",
    ],
    # human_review 默认模板
    "human_review_default": {
        "reviewer_group": "group_3",
        "problem_valid": None,
        "wrong_model_names": [],
        "corrected_reasoning_trace": "",
        "corrected_code": "",
        "corrected_execution_result": None,
        "notes": "",
        "final_accept": None,
    },
}

# ============================================================
# 一致性分组 → prompt 文件映射
# ============================================================
CONSISTENCY_PROMPTS = {
    "agree_all":      "agree_all.txt",
    "agree_two":      "agree_two.txt",
    "disagree_all":   "disagree_all.txt",
    "missing":        "missing.txt",
}

# ============================================================
# 一致性分组说明
# ============================================================
CONSISTENCY_LABELS = {
    "agree_all":    "三模型目标值一致",
    "agree_two":    "两个一致一个不同",
    "disagree_all": "三个目标值全不同",
    "missing":      "有模型缺失目标值",
}
