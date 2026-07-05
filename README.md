# Group 3 数据校对 Pipeline

## 快速开始

```bash
# 1. 安装依赖
pip install coptpy

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入真实的 API_KEY

# 3. 批量运行（提取 + 按一致性分类 + AI 填充 human_review）
python3 scripts/run_ai_review.py --start 1 --end 50

# 4. 人工复核 work/ 下的文件，对 feasible 的题目本地运行 corrected_code

# 5. 写回 + 校验
python3 scripts/writeback.py --start 1 --end 50
```

## 脚本一览

### Pipeline 脚本

| 脚本 | 作用 | 用法 |
|------|------|------|
| 🔰 **`run_ai_review.py`** | 主入口：提取 + 分类 + AI 填充 | `--start 1 --end 50`，`--no-ai` 跳过 AI |
| `fill_human_review.py` | 独立对已有文件重跑 AI | `--input work/agree_two.json` |
| `writeback.py` | 批量写回标准 JSONL + 校验 | `--start 1 --end 50`，`--validate-only` 仅校验 |

### 辅助工具脚本（对标数据校对指南）

| 脚本 | 作用 | 用法 |
|------|------|------|
| `extract_review_item.py` | 按 problem_id 抽取单题到可读 JSON | `--problem-id v_xxx` |
| `replace_human_review.py` | 将单题 human_review 写回原始 JSONL | `--problem-id v_xxx --human-review work/v_xxx.json` |
| `validate_final.py` | 独立校验最终 JSONL | `--input output/xxx.jsonl --compare group_3.jsonl` |

## 推荐人工校对工作流

### 方式一：批量 AI 辅助（效率优先）

```
run_ai_review.py → 人工复核 work/*.json → writeback.py
```

适合一次性处理几十上百条数据。

### 方式二：单题精细审阅（质量优先）

```bash
# 1. 抽取单题
python3 scripts/extract_review_item.py --problem-id v_ff0f36fb6000ab
# → work/v_ff0f36fb6000ab.json（只含 question/auto_reject/review_guidance/model_responses/human_review）

# 2. 人工 + AI 辅助填写 human_review

# 3. 本地运行 corrected_code，将结果填入 corrected_execution_result

# 4. 写回
python3 scripts/replace_human_review.py --problem-id v_ff0f36fb6000ab --human-review work/v_ff0f36fb6000ab.json
```

### 方式三：混合使用

先用批量 AI 跑完大部分，遇到疑难题目用单题工具精细处理。

## 文件结构

```
stage1/
├── .env.example           # API Key 模板（复制为 .env 后填写）
├── .env                   # 真实 API Key（Git 忽略）
├── config.py              # 集中配置（组名、路径、模型参数）
├── prompts/               # 4 个分策略 prompt（按一致性选用）
│   ├── agree_all.txt      # 三模型一致
│   ├── agree_two.txt      # 两个一致
│   ├── disagree_all.txt   # 全不一致
│   └── missing.txt        # 有模型失败
├── scripts/
│   ├── run_ai_review.py   # 🔰 主入口
│   ├── fill_human_review.py
│   ├── writeback.py
│   ├── extract_review_item.py    # 单题提取
│   ├── replace_human_review.py   # 单题写回
│   └── validate_final.py         # 独立校验
├── group_3.jsonl          # 原始数据
├── work/                  # 工作产出（Git 忽略）
│   ├── agree_all.json     # 可读 JSON，AI 已填充 human_review
│   ├── agree_two.json
│   ├── disagree_all.json
│   └── missing.json
└── output/                # 最终产出（Git 忽略）
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

## 切换小组

编辑 `config.py`，修改 `REVIEW_CONFIG["reviewer_group"]` 即可。
