#!/usr/bin/env python3
"""给"大肥鱼本尊"那 17 张贴图补更全的标签（同义词），让选片更能对上日常说法。

按 desc 里含"大肥鱼"来找条目，不依赖 id 顺序。
"""
from __future__ import annotations

import json
import os

INDEX = "/opt/deepsea/data/plugins/stickers/index.json"

# 描述关键词 → 追加的标签
EXTRA = {
    "委屈巴巴": ["委屈", "难过", "饿", "可怜", "无语", "服了"],
    "平常站立": ["微笑", "平常", "打招呼", "在"],
    "扒着盆边哭": ["哭", "难过", "委屈", "可怜", "泪"],
    "记笔记": ["记笔记", "认真", "学习", "记下"],
    "眼睛瞪大": ["震惊", "被点名", "无语", "惊讶", "服了", "离谱"],
    "爆米花": ["吃瓜", "看戏", "围观", "乐", "笑死"],
    "吃西瓜": ["吃", "西瓜", "夏天"],
    "皱眉": ["皱眉", "不高兴", "嫌弃", "无语", "服了", "逗我"],
    "摇扇子": ["得意", "自信", "腹黑", "悠哉"],
    "探头": ["探头", "偷看", "在吗", "冒头"],
    "小风扇": ["热", "累", "摆烂", "夏天", "卷不动了"],
    "棒棒糖": ["摆烂", "卖萌", "躺平", "糖", "卷不动了", "佛系"],
    "红色按钮": ["手贱", "按", "试试", "好奇"],
    "正在思考": ["思考", "发呆", "想想", "疑惑"],
    "端着西瓜笑": ["开心", "笑", "吃", "好耶", "笑死"],
    "老吴老吴": ["撒娇", "内个内个", "卖萌", "哼哼"],
}


def main() -> int:
    d = json.load(open(INDEX, encoding="utf-8"))
    changed = 0
    for it in d["items"]:
        desc = str(it.get("desc") or "")
        for key, extra in EXTRA.items():
            if key in desc:
                tags = list(dict.fromkeys(list(it.get("tags") or []) + extra))[:10]
                if tags != (it.get("tags") or []):
                    it["tags"] = tags
                    changed += 1
                break
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, INDEX)
    print("更新了 %d 条标签" % changed)
    for it in d["items"][:3]:
        print("  ", it["desc"], "→", "/".join(it["tags"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
