从以下临时笔记中提取关键信息。输出为严格的 JSON 格式，不要添加任何额外说明。

要求：
1. 总结笔记的核心内容（summary）
2. 提取关键点（key_points），每条一句话
3. 提取行动项（action_items），如有
4. 提取重要决策（decisions），如有
5. 提取涉及的项目（projects_mentioned）
6. 评估信息质量（confidence 0-1）

输出 JSON 格式：
{
  "summary": "...",
  "key_points": ["...", "..."],
  "action_items": ["...", "..."],
  "decisions": [{"decision": "...", "reasoning": "...", "alternatives": "..."}],
  "projects_mentioned": ["...", "..."],
  "tags": ["...", "..."],
  "confidence": 0.0-1.0
}
