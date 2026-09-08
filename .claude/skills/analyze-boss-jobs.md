# analyze-boss-jobs

分析 BOSS直聘爬取的职位数据，输出职位需求频次、薪资范围、公司质量综合评分的 Markdown 报告。

## 触发场景
- 用户说"分析职位需求"、"分析爬取结果"、"需求统计"、"生成需求报告"
- 用户想知道某个岗位最常要求哪些技能/经验，以及这些要求对应的薪资水平

## 执行步骤

1. 确认工作目录为项目根目录（`C:\Dev\projects\pixelclaw`）
2. 运行以下命令：
   ```bash
   python scenarios/boss/scripts/analyze_requirements.py
   ```
   - 按关键词过滤：`--keyword "AI产品经理"`
   - 强制重新提取（忽略缓存）：`--force`
   - 自定义数据目录：`--output-dir path/to/output`

3. 等待脚本完成（每条职位会调用一次 Claude Haiku API 提取需求）
4. 告知用户报告路径，并将 Markdown 报告内容展示给用户

## 前置条件
- `ANTHROPIC_API_KEY` 环境变量已设置
- `anthropic` Python 包已安装（`pip install anthropic`）
- 数据来源（二选一，优先级从高到低）：
  1. `scenarios/boss/output/requirements.db` 的 `job_details` 表（运行过 `scrape_job_details.py` 后自动写入）
  2. `scenarios/boss/output/` 下的 `job_details_*.json` 历史文件（回落路径，不再主动生成）

## 输出
- SQLite 数据库：`scenarios/boss/output/requirements.db`（增量，重复运行自动跳过已处理职位）
- Markdown 报告：`scenarios/boss/output/requirements_analysis_{keyword}_{date}.md`

## 报告内容说明

报告按关键词分节，每节下按需求类别（技术技能、产品能力、行业经验、学历要求、软素质）分组，
表格列：需求 | 频次 | 频率 | 月薪范围 | 公司质量(0-8) | 综合分 | 代表公司

**综合分计算**（满分10分）：
- 频率占 40%：该需求出现在多少比例的职位中
- 薪资占 30%：要求该技能的职位薪资水平（10K→0分，50K→满分）
- 公司质量占 30%：要求该技能的公司融资阶段+规模评分
- 加分项打七折，在独立区块展示
