从以下简历中提取关键信息，仅返回 JSON，无其他文字：

{resume}

输出格式（每个字段都必须填写，数组字段至少 2 项）：
{{
  "name": "<姓名>",
  "current_title": "<当前求职目标，如「FDE型产品经理 / AI产品经理」>",
  "product_exp_years": <产品/FDE/AI相关工作年数，整数>,
  "role_type": ["<角色类型，从以下选填：B端产品、C端产品、AI产品、FDE、设计、售前、平台产品、技术PM>", ...],
  "product_track": ["<产品方向，如 0到1、平台产品、Agent OS、ToB SaaS、ToC AI工具、多租户>", ...],
  "industry_verticals": ["<涉及的行业垂直领域，如 EHS合规、能碳管理、工业SaaS、智能穿戴、出海AI>", ...],
  "ai_specialties": ["<AI产品专项能力，如 Agent OS架构、统一AI网关、MCP集成、Prompt Engineering、Task Routing/Fallback、n8n工作流、LLM编排>", ...],
  "tech_skills": ["<前端/后端/设计/自动化工具技能，如 React/TypeScript、Figma、Python、Supabase、Vibe Coding>", ...],
  "key_achievements": [
    "<量化成就，必须含具体数字，如「3个月×4人完成能碳平台0到1 UAT交付（2026.10正式上线）」>",
    "<量化成就2，如「n8n工作流将调研效率提升70%」>",
    "<量化成就3，如「深创赛第二名（9705票）」>"
  ],
  "unique_strengths": [
    "<与普通PM差异化的独特优势，具体描述，如「团队唯一PM兼设计+售前，对交付节奏有完整主导权」>",
    "<独特优势2，如「Vibe Coding高保真Demo可同时支撑研发联调和售前演示」>"
  ],
  "education": "<最高学历+学校，如「爱丁堡大学建筑及都市设计硕士 + 台湾科技大学计算机科学辅修GPA4.0」>",
  "career_story": "<简短职业路径叙事，如「建筑师5年（空间抽象+系统思维）→ AI产品经理2年（FDE型，兼设计+售前）」>",
  "seniority": "<职级判断，如「mid-senior（小团队3-5人，独立主导）」或「junior」或「senior」>",
  "target_roles": ["<目标职位类型，如 AI产品经理、FDE、0到1产品负责人、AI Agent产品>", ...],
  "target_salary_min_k": <期望月薪下限（K），整数，无明确期望填0>,
  "preferred_location": "<城市，如「上海（可接受北京/杭州）」>"
}}
