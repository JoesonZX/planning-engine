# planning-engine

个人规划 vault 的 agent 引擎：markdown 文件是唯一事实源，本仓库只提供解析与报告生成，**不包含任何个人数据**。

配套数据仓模板见 README「接入你的 vault」。

## 它做什么

- `parser.py` — 最小语法解析器（冻结语法）：
  - checkbox：`- [ ]` / `- [x]`
  - 行内日期：`M/D` 或 `M.D`（带防御：小数、版本号、时间不会被误认）
  - `⭐` 硬节点标记
- `report.py` — 晚间报告（`tomorrow.md`）六大板块 + 根目录 `仪表盘.md`；可选 GLM「今晚摘要」（预算帽 $3/月，超帽自动降频为周日一次）
- `triage.py` — 周日 20:00 inbox 分拣：私人内容（情绪/感情关键词）代码侧拦截、LLM 拿不准强制 HOLD、白名单校验三重防御；失败 = 全部 hold，数据永不丢失
- `weekly_review.py` — 周复盘 `week-YYYY-Www.md` 七板块（只统计不评判）+ GLM 起草「下周三件事」
- `.github/workflows/evening.yml` — reusable：每晚 21:00（PT）报告 + 仪表盘
- `.github/workflows/weekly.yml` — reusable：周日 20:00（PT）分拣 + 周复盘
- 两个 workflow 都由数据仓调用，用调用方自带 `GITHUB_TOKEN` 提交，无需任何 PAT

## 接入你的 vault

1. 建一个**私有**数据仓（如 `planning-data`），放你的 markdown 文件，外加：
   - `.agent-config.yml`（timezone / skip_files / 模型 / 预算）
   - `inbox.md`（手机随手记落点）
   - `.github/workflows/evening.yml`：

     ```yaml
     name: evening
     on:
       schedule:
         - cron: "0 4,5 * * *"   # 21:00 America/Los_Angeles（PDT=4 UTC，PST=5 UTC）
       workflow_dispatch:
     permissions:
       contents: write
     jobs:
       report:
         uses: <你的用户名>/planning-engine/.github/workflows/evening.yml@main
         with:
           force: ${{ github.event_name == 'workflow_dispatch' }}
         secrets: inherit
     ```

2. 数据仓 Secrets 里配 `GLM_API_KEY`（bigmodel 开放平台）
3. 手动触发一次验证：Actions → evening → Run workflow

## 隐私

- 引擎仓公开安全：它从不接触数据仓内容（运行时 checkout 的调用方仓库只存在于 Actions 日志之外的处理过程）
- 数据仓里情绪/感情类文件写进 `.agent-config.yml` 的 `skip_files`，引擎既不解析也不发送给 LLM

## 本地开发

```bash
python -m unittest discover -s tests      # 单测
python report.py --vault /path/to/data --force   # 本地强制生成
```
