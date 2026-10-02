"""提示词模板。共同要求：结构化 JSON 输出 + 每条结论必须附「来源锚点」（原话），
  参考 note-slides 的 source-anchor 思路 —— 校验不过的一律丢弃，宁可少写不许编。"""

ANCHOR_RULE = """硬性要求：
1. 只写聊天记录里**有依据**的事实；不确定的宁可不写；绝不编造。
2. 每条结论都要在 evidence 里附 1~3 句**照抄的原始消息片段**（连续 6 个字以上照抄，不要改写、不要加引号）。
3. 不评价人、不给群友贴标签（不许写"这人爱吹牛""很烦"这类判断），只描述可观察到的话题、风格、习惯用词。
4. 每条 ≤ {limit} 字，中文，口语化，别用书面总结腔。
5. 严格输出 JSON，不要输出任何 JSON 以外的文字。"""

GROUP_PROFILE = """你在帮一个 QQ 群机器人维护「对本群的印象」。读下面的群聊记录，然后输出这个群的整体印象。

分三个方面写：
① 这群人主要聊什么（话题、兴趣、常出现的具体名词）
② 说话风格、常用词/梗
③ 最近在关注的事

{anchor_rule}

输出 JSON 格式：
{{"profile": "①...\\n②...\\n③...", "evidence": ["原话1", "原话2", "原话3"]}}

=== 群聊记录开始 ===
{material}
=== 群聊记录结束 ==="""

MEMBERS = """你在帮一个 QQ 群机器人维护「对群友的印象」。下面是群里每个人最近的发言汇总，请为**每个人**写一句印象。

每个人的印象包含：平时聊什么、说话风格/口头禅，≤{limit} 字。没有足够信息的（发言少于 3 条）就不要写他。

{anchor_rule}
6. 只给下面出现的人写，不要新增没出现过的人。

输出 JSON 格式：
{{"members": [{{"name": "昵称", "profile": "印象", "evidence": ["该人原话1", "该人原话2"]}}]}}

=== 各人发言汇总开始 ===
{material}
=== 各人发言汇总结束 ==="""


def group_prompt(material: str, limit: int = 200) -> str:
    return GROUP_PROFILE.format(anchor_rule=ANCHOR_RULE.format(limit=limit), material=material)


def members_prompt(material: str, limit: int = 120) -> str:
    return MEMBERS.format(anchor_rule=ANCHOR_RULE.format(limit=limit),
                          limit=limit, material=material)
