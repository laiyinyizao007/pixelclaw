从以下简历中提取关键信息，仅返回 JSON，无其他文字：

{resume}

输出格式：
{{
  "name": "<姓名>",
  "title": "<当前目标职位>",
  "total_exp_years": <产品/设计/技术相关工作年数，整数>,
  "core_skills": ["技能1", "技能2", ...],
  "domains": ["领域1", "领域2", ...],
  "recent_achievements": ["成就1", "成就2", "成就3"],
  "target_salary_min_k": <期望月薪下限（K），整数，无明确期望填 0>,
  "preferred_location": "<城市名，如上海>"
}}