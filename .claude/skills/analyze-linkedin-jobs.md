# analyze-linkedin-jobs

分析 LinkedIn 爬取的职位数据，输出职位需求频次、类别分布、综合评分的 Markdown 报告。

## 触发场景
- 用户说"分析 LinkedIn 职位"、"分析 LinkedIn 爬取结果"、"LinkedIn 需求统计"
- 用户想知道某个岗位最常要求哪些技能/经验

## 执行步骤

1. 确认工作目录为项目根目录（`C:\Dev\projects\pixelclaw`）
2. 运行以下命令：
   ```bash
   python scenarios/linkedin/scripts/analyze_requirements.py
   ```
   - 按关键词过滤：`--keyword "Product Manager"`
   - 强制重新提取（忽略缓存）：`--force`
   - 自定义数据库：`--db path/to/linkedin_jobs.db`

3. 等待脚本完成（每条职位调用一次 Claude Haiku API 提取需求）
4. 告知用户报告路径，并将 Markdown 报告内容展示给用户

## 前置条件
- `ANTHROPIC_API_KEY` 环境变量已设置
- `anthropic` Python 包已安装（`pip install anthropic`）
- 数据来源：`scenarios/linkedin/output/linkedin_jobs.db` 的 `job_details` 表
  （需先运行 `scrape_job_details.py` 写入数据）

## 输出
- SQLite 数据库：`scenarios/linkedin/output/linkedin_jobs.db`（增量，重复运行自动跳过已处理职位）
- Markdown 报告：`scenarios/linkedin/output/requirements_analysis_{keyword}_{date}.md`

## 报告内容说明

报告按关键词分节，每节下展示高频需求 TOP 30，列：
需求 Tag | 类别 | 频次 | 频率 | 质量分 | 综合分

**综合分计算**（满分1.0）：
- 频率占 55%（`freq_score = 出现职位数 / 总职位数`）
- 质量占 45%（`quality_score` 基于类别权重：技术技能/产品能力=1.0，行业经验=0.9，学历=0.7，软素质=0.6）

与 Boss 版本的差异：LinkedIn 无薪资数据，故评分公式移除薪资维度。
