# AGENTS.md — planning-engine（公开引擎仓）

个人规划 vault 的解析与报告引擎：md 是数据，本仓只有代码。变更经维护者 diff 审查后接受。

## 完成定义

- 任何改动：`python -m unittest discover -s tests` 全绿才算完成
- 重构前先 `python tests/capture_golden.py` 固化当前输出，改完 golden 重捕比对
- 加新产出物 = 写一个 `render_x(snap)` 函数 + 一行 CLI，不复制加载逻辑
- parser.py 语法冻结：改语法=破坏所有下游仓，需单独提案

## 纪律

- llm.py 是全引擎唯一 GLM 出口：新功能不得另开请求路径，预算记账必经 usage
- 提交规范：`vN: 要点——细节；测试 N 绿`；复查发现的修复单独提交（`vN 复查修复: …`），不混入功能提交
- 本仓公开安全：不写入任何个人数据、本地路径、密钥
