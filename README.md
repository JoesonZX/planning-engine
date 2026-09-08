# planning-engine

个人规划 vault 的 agent 引擎：markdown 文件是唯一事实源，本仓库只提供解析与报告生成，**不包含任何个人数据**。

配套数据仓模板见 README「接入你的 vault」。

## 它做什么

- `parser.py` — 最小语法解析器（冻结语法）：
  - checkbox：`- [ ]` / `- [x]`
  - 行内日期：`M/D` 或 `M.D`（带防御：小数、版本号、时间不会被误认）
  - `⭐` 硬节点标记
- `report.py` — 晚间报告（`tomorrow.md`）六大板块：明日事项 / 未来 7 天死线 / 滑落项 / 今日完成 / inbox 未分拣 / 近 7 天改动文件；可选 GLM「今晚摘要」（预算帽 $3/月，超帽自动降频为周日一次）
- `.github/workflows/evening.yml` — reusable workflow：由数据仓调用，跑测试 → 生成报告 → commit 回数据仓（用调用方自带的 `GITHUB_TOKEN`，无需任何 PAT）

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
