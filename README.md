# Group 3 数据校对 Pipeline

## 快速开始

```bash
# 1. 安装依赖
pip install coptpy

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env，填入真实的 API_KEY

# 3. 一键运行（提取 + 分类 + AI 填充 → .md 审阅文件）
python3 scripts/run_ai_review.py --start 1 --end 50

# 4. 逐题人工复核 work/ 下的 .md 文件
#    每个 .md 包含三个可编辑区域：
#    - corrected_reasoning_trace（纯文本段落）
#    - corrected_code（```python 代码块）
#    - review_json（决策字段）
#    对 feasible 题本地运行 corrected_code，填入 corrected_execution_result

# 5. 批量写回
python3 scripts/replace_human_review.py --batch --start 1 --end 50

# 6. 校验
python3 scripts/validate_review_file.py --input output/group_3_reviewed_1_50.jsonl
```

## 核心原则

**AI 辅助初稿，人工对数据负责。** Pipeline 绝不会跳过人工复核步骤——每条数据都必须经过人类逐题确认后才能写回。

## 脚本一览

| 脚本 | 作用 | 用法 |
|------|------|------|
| 🔰 **`run_ai_review.py`** | 主入口：提取 + 分类 + AI 填充 → .md 文件 | `--start 1 --end 50`，`--no-ai` 跳过 AI |
| `ai_review_one.py` | 对已有 .md 文件（重新）调 AI | `--input work/agree_two/v_xxx.md` 或 `--batch work/agree_two` |
| `extract_review_item.py` | 按 problem_id 抽取单题到 .md | `--problem-id v_xxx` |
| `replace_human_review.py` | 将人工复核后的 human_review 写回 JSONL | 单题: `--input work/…/v_xxx.md`；批量: `--batch --start 1 --end 50` |
| `validate_review_file.py` | 校验 JSONL（字段/类型/完整性） | `--input output/xxx.jsonl --compare group_3.jsonl` |

## .md 审阅文件格式

每个 `.md` 文件结构：

```
---
problem_id: v_xxx
category: agree_two
---

## 题目                      ← 只读
## 拒绝原因 & 共识            ← 只读
## 审查指导                   ← 只读
## 模型求解轨迹               ← 只读（三个模型的完整 CoT + 代码）

---

## corrected_reasoning_trace  ← ✍️ 人工编辑（纯文本，可直接换行）
## corrected_code              ← ✍️ 人工编辑（```python 代码块，自然换行）
## human_review                ← ✍️ 人工编辑（review_json，仅决策短字段）
```

推理和代码不放在 JSON 字符串中，因此**没有 `\n` 转义问题**，代码块在 VS Code 中带语法高亮。

## 推荐工作流

### 方式一：批量 AI 辅助 ✅

```
run_ai_review.py --start 1 --end 50
  → work/agree_all/v_xxx.md
  → work/agree_two/v_xxx.md
  → work/disagree_all/v_xxx.md
  → work/missing/v_xxx.md

逐题打开 .md，复核 AI 初稿
  → 检查 reasoning、修正 code
  → 本地运行 corrected_code，填入 corrected_execution_result
  → 修改 review_json 中的决策字段

replace_human_review.py --batch --start 1 --end 50
  → output/group_3_reviewed_1_50.jsonl

validate_review_file.py --input output/group_3_reviewed_1_50.jsonl
  → ✅ 或返回错误清单
```

### 方式二：单题精细审阅

```bash
# 抽取单题
python3 scripts/extract_review_item.py --problem-id v_ff0f36fb6000ab

# 可选：单独调 AI
python3 scripts/ai_review_one.py --input work/v_ff0f36fb6000ab.md

# 人工填写后写回
python3 scripts/replace_human_review.py --input work/v_ff0f36fb6000ab.md
```

## 文件结构

```
stage1/
├── .env.example              # API Key 模板
├── .env                      # 真实 Key（Git 忽略）
├── config.py                 # 集中配置
├── prompts/                  # 4 个分策略 prompt
│   ├── agree_all.txt         # 三模型一致
│   ├── agree_two.txt         # 两个一致
│   ├── disagree_all.txt      # 全不一致
│   └── missing.txt           # 有模型失败
├── scripts/
│   ├── run_ai_review.py          # 🔰 主入口
│   ├── ai_review_one.py          # 独立 AI 重跑
│   ├── extract_review_item.py    # 单题提取
│   ├── replace_human_review.py   # 写回 JSONL
│   └── validate_review_file.py   # 校验 JSONL
├── group_3.jsonl             # 原始数据
├── work/                     # 👈 人工工作目录（Git 忽略）
│   ├── agree_all/            # .md 文件，按一致性分类
│   ├── agree_two/
│   ├── disagree_all/
│   └── missing/
└── output/                   # 最终 JSONL（Git 忽略）
    └── group_3_reviewed_1_50.jsonl
```

## 数据按一致性分类

| 分组 | 含义 | 审查策略 |
|------|------|---------|
| **agree_all** | 三模型目标值一致 | 快审：验证 feasible，直接复制轨迹 |
| **agree_two** | 两个一致，一个不同 | 标准：少数派大概率有错 |
| **disagree_all** | 三个都不一样 | 深度：逐一分析谁正确 |
| **missing** | 有模型执行失败 | 调试：先看失败原因 |

## 人工复核检查清单

- [ ] `corrected_reasoning_trace` 推理完整、建模正确
- [ ] `corrected_code` 可运行，本地执行通过
- [ ] `corrected_execution_result` 已填入运行结果
- [ ] `problem_valid` 判断正确
- [ ] `wrong_model_names` 标注准确
- [ ] `notes` 有说明
- [ ] `final_accept` 与内容一致

## 切换小组

编辑 `config.py`，修改 `REVIEW_CONFIG["reviewer_group"]` 和 `PATHS["input_jsonl"]` 即可。
