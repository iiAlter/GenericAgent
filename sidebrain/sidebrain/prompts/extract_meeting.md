从以下会议纪要中提取关键信息。输出为严格的 JSON 格式，不要添加任何额外说明。

要求：
1. 总结会议的核心目的（summary）
2. 提取关键讨论点（key_points），每条一句话
3. 提取所有行动项（action_items），包括负责人（如有）
4. 提取重要决策（decisions），包含决策内容、理由和备选方案
5. 提取参会人物（people_mentioned）
6. 提取涉及的项目（projects_mentioned）
7. 评估信息质量（confidence 0-1）

输出 JSON 格式：
{
  "summary": "...",
  "key_points": ["...", "..."],
  "action_items": ["...", "..."],
  "decisions": [{"decision": "...", "reasoning": "...", "alternatives": "..."}],
  "people_mentioned": ["...", "..."],
  "projects_mentioned": ["...", "..."],
  "tags": ["...", "..."],
  "confidence": 0.0-1.0
}
