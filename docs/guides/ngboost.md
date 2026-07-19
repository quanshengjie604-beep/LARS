# Startup Probability Decision Engine

本项目实现 [`data-requirements.md`](../requirements/data-requirements.md) 中 NGBoost 团队负责的部分：接收 Screening 团队交付的 JSONL，完成校验、展平、编码、M1–M6 训练，并向 Monte Carlo 模块输出统一概率分布。

## Pipeline 与分工

```text
Screening 团队
  收集/冻结特征，生成三轴评分，追踪结果，维护证据与删失信息
        │ screening_handover/*.jsonl
        ▼
本项目（NGBoost 团队）
  契约校验 → snapshot_id 关联 → 防泄漏检查 → 展平/缺失处理/编码
        → M1-M6 子集选择 → 概率模型训练 → 模型版本化
        → 实时校验/推理 → 统一概率 JSONL
        ▼
Monte Carlo 团队
  消费分布类型、参数、分位数/生存曲线和数据质量置信度，完成组合模拟
```

边界原则：本项目不填造原始缺失值、不回写 Screening 评分、不把未来结果混入特征，也不负责 Monte Carlo 的投资组合假设。预处理器内部的统计插补仅用于模型计算，原始缺失计数仍作为特征与输出告警。

## 六个模型

| ID | 任务 | 训练子集 | 统一输出 |
|---|---|---|---|
| M1 | 24 个月内下一轮 | 非空标签，排除右删失 | 类别概率 |
| M2 | 下一事件时间 | duration + event indicator | 生存曲线 |
| M3 | 失败时间 | duration + event indicator | 生存曲线 |
| M4 | 年收入增长因子 | 两次可比收入、正值标签 | LogNormal 参数和分位数 |
| M5 | 退出类型 | 完整退出类别 | 类别概率 |
| M6 | 退出价值 | 披露估值或交易额 | LogNormal 参数和分位数 |

M2/M3 优先使用 `scikit-survival`；其他适用任务优先使用 NGBoost。`strict_backend: false` 时，缺少可选依赖的开发环境会使用 sklearn 或 Kaplan–Meier 基线，manifest 会明确记录 backend；生产建议设为 `true`。

## 使用

```powershell
python -m pip install -e ".[dev]"
decision-engine --config config/default.yaml validate
decision-engine --config config/default.yaml train
decision-engine --config config/default.yaml infer --output artifacts/predictions.jsonl
pytest
```

某模型可用标签少于 `training.minimum_rows` 时，训练不会伪造数据，而会在 manifest 的 `skipped` 中记录原因。因此 M4/M6 可安全延后上线。

## Monte Carlo 输出契约

每个请求输出一行 JSON，保留三个 ID。`models` 中：M1/M5 为 `categorical`；M2/M3 为 `survival_curve`；M4/M6 为 `lognormal`。同时输出模型生成时间、输入数据质量置信度、缺失/矛盾数和校验告警。

`model_confidence` 是输入证据完整度指标，不冒充概率校准指标。正式评估应使用时间切分验证集报告 Brier score、log loss、C-index 和校准曲线。

## 目录

```text
config/default.yaml                 参数、路径、模型开关
src/decision_engine/validation.py   数据契约与 join 校验
src/decision_engine/features.py     展平与标签提取
src/decision_engine/models.py       概率/生存模型适配器
src/decision_engine/pipeline.py     M1-M6 训练和统一推理
src/decision_engine/cli.py          validate/train/infer CLI
tests/                              单元测试
```
