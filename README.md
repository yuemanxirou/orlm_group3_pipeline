# Group 3 数据校对 Pipeline

## 快速开始

```bash
# 1. 安装依赖
pip install coptpy

# 2. 修改 config.py 中的 group_name 和 api_key

# 3. 一键运行（提取 + 按一致性分类 + AI 填充 human_review）
python3 scripts/run_ai_review.py --start 1 --end 50

# 4. 人工复核 work/ 下的文件，对 feasible 的题目本地运行 corrected_code

# 5. 写回 + 校验
python3 scripts/writeback.py --start 1 --end 50
```

## 三个脚本

| 脚本 | 作用 | 用法 |
|------|------|------|
| 🔰 **`run_ai_review.py`** | 主入口：提取 + 分类 + AI 填充 | `--start 1 --end 50`（闭区间），`--no-ai` 跳过 AI |
| `fill_human_review.py` | 独立对已有文件重跑 AI | `--input work/agree_two.json` |
| `writeback.py` | 写回标准 JSONL + 校验 | `--start 1 --end 50`，`--validate-only` 仅校验 |

## 工作目录

```
stage1/
├── config.py              # 配置（API key、组名、路径）
├── prompts/               # 4 个分策略 prompt
│   ├── agree_all.txt      # 三模型一致
│   ├── agree_two.txt      # 两个一致
│   ├── disagree_all.txt   # 全不一致
│   └── missing.txt        # 有模型失败
├── scripts/
│   ├── run_ai_review.py   # 🔰 主入口
│   ├── fill_human_review.py
│   └── writeback.py
├── group_3.jsonl          # 原始数据（不提交 Git）
├── work/                  # 工作产出（不提交 Git）
│   ├── agree_all.json     # 可读 JSON，AI 已填充 human_review
│   ├── agree_two.json
│   ├── disagree_all.json
│   └── missing.json
└── output/                # 最终产出（不提交 Git）
    └── group_3_reviewed_1_50.jsonl
```

## 数据按一致性分类

| 分组 | 含义 | 审查策略 |
|------|------|---------|
| **agree_all** | 三模型目标值一致 | 快审：验证 feasible，直接复制轨迹 |
| **agree_two** | 两个一致，一个不同 | 标准：少数派大概率有错 |
| **disagree_all** | 三个都不一样 | 深度：逐一分析谁正确 |
| **missing** | 有模型执行失败 | 调试：先看失败原因 |

## 人工校对步骤

1. 打开 `work/agree_all.json` 等文件（VS Code 可读，代码块自然换行）
2. 查看 `human_review` 中 AI 的初稿
3. 对 `final_accept: true` 的题目，复制 `corrected_code` 本地运行
4. 运行结果填入 `corrected_execution_result`
5. 如有问题，修改 `wrong_model_names`、`notes` 等其他字段
6. 运行 `python3 scripts/writeback.py --start 1 --end 50` 产出最终 JSONL
