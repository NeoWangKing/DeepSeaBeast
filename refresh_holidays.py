#!/usr/bin/env python3
"""抓取中国法定节假日休息日，写入 holidays.json（供空闲/高峰判断用）。"""
import datetime
import json
import os
import sys
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "holidays.json")
URL = "https://gh-proxy.com/https://raw.githubusercontent.com/NateScarlet/holiday-cn/master/{year}.json"


def fetch(year: int) -> dict:
    req = urllib.request.Request(URL.format(year=year), headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    year = datetime.date.today().year
    off_days: dict[str, str] = {}
    for y in (year, year + 1):
        try:
            data = fetch(y)
        except Exception as e:
            print(f"[warn] {y} 抓取失败: {e!r}", file=sys.stderr)
            continue
        for item in data.get("days", []):
            if item.get("isOffDay"):
                off_days[item["date"]] = item.get("name", "")
    if not off_days:
        print("没抓到任何节假日数据，保留旧文件", file=sys.stderr)
        return 1
    payload = {
        "updatedAt": datetime.datetime.now().isoformat(timespec="seconds"),
        "years": [year, year + 1],
        "offDays": dict(sorted(off_days.items())),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"已写入 {OUT}: {len(off_days)} 个休息日（{year}/{year+1}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
