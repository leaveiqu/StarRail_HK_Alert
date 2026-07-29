# -*- coding: utf-8 -*-
"""
星穹鐵道 香港聯動消息 自動監控腳本
資料源:
  1. HoYoLAB 官方星穹鐵道公告 RSS (feeds.c3kay.de,每30分鐘更新,實測可用)
  2. Facebook 崩壞：星穹鐵道 繁中官方專頁 (透過 RSSHub 轉 RSS,best-effort,公開節點可能被限流)

注意:米哈遊啟動器 content API (getAllGameBasicInfo) 需要未公開的啟動器 key,
     無法在沒有金鑰的情況下取得資料,故不採用此路線。

流程:抓取 -> 關鍵字比對(必須同時命中「香港」+「聯動類詞」) -> 與 sent_ids.json 去重 -> 推送 Discord -> 更新 sent_ids.json
"""

import json
import os
import re
import sys
import hashlib
import datetime
from pathlib import Path

import requests
import feedparser

# ---------------------------------------------------------------------------
# 設定區
# ---------------------------------------------------------------------------

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")

# 關鍵字規則:必須同時符合「地區詞」與「聯動類型詞」其中之一,才算命中
REGION_KEYWORDS = ["香港", "HK", "Hong Kong"]
COLLAB_KEYWORDS = [
    "聯動", "联动", "快閃", "快闪", "期間限定店", "期间限定店",
    "線下活動", "线下活动", "線下", "线下", "展覽", "展览",
    "主題店", "主题店", "期間限定", "期间限定", "pop-up", "POP-UP", "Pop-up",
    "cafe", "咖啡店", "咖啡廳", "咖啡厅", "商店", "特別店",
    # 英文版(HoYoLAB 官方公告以英文為主)
    "offline event", "Offline Event", "collab", "Collab", "collaboration",
    "Collaboration", "pop-up store", "meetup", "meet-up", "exhibition",
    "Exhibition", "fan meet", "HoYo FEST", "themed store", "merchandise store",
]

SENT_IDS_FILE = Path(__file__).parent / "sent_ids.json"
MAX_KEEP_IDS = 800  # 避免檔案無限成長,只保留最近 N 筆

# HoYoLAB 官方星穹鐵道公告 RSS(第三方長期維護、每30分鐘更新一次,實測可用)
HOYOLAB_FEED_URL = "https://feeds.c3kay.de/starrail.xml"

# RSSHub 公開節點,如果失效可換成自架節點或其他公開節點
RSSHUB_BASE = os.environ.get("RSSHUB_BASE", "https://rsshub.app")
FB_PAGE_ID = "HonkaiStarRail.CHT"  # 崩壞：星穹鐵道 繁中官方 Facebook 專頁


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

def make_id(link: str) -> str:
    """用連結產生穩定的去重用 ID"""
    return hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


def load_sent_ids() -> set:
    if SENT_IDS_FILE.exists():
        try:
            data = json.loads(SENT_IDS_FILE.read_text(encoding="utf-8"))
            return set(data.get("ids", []))
        except Exception:
            return set()
    return set()


def save_sent_ids(ids: set):
    ids_list = list(ids)[-MAX_KEEP_IDS:]
    SENT_IDS_FILE.write_text(
        json.dumps({"ids": ids_list, "updated_at": datetime.datetime.utcnow().isoformat()}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


REGION_PATTERN = re.compile(r"(香港|\bHK\b|\bHong\s+Kong\b)", re.IGNORECASE)


def is_hit(text: str) -> bool:
    """必須同時符合:(1) 明確提到香港 (2) 出現聯動/線下活動類詞彙,兩者缺一不可。
    這樣可以避免新加坡、馬來西亞、菲律賓等其他地區的活動被誤判為香港消息。"""
    if not text:
        return False
    has_region = bool(REGION_PATTERN.search(text))
    text_lower = text.lower()
    has_collab = any(k.lower() in text_lower for k in COLLAB_KEYWORDS)
    return has_region and has_collab


def trim_summary(text: str, length: int = 100) -> str:
    if not text:
        return "(無摘要,請點擊連結查看詳情)"
    # 去除 HTML tag
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > length:
        clean = clean[:length] + "…"
    return clean or "(無摘要,請點擊連結查看詳情)"


# ---------------------------------------------------------------------------
# 各資料源抓取
# ---------------------------------------------------------------------------

def fetch_hoyolab_official_feed() -> list:
    """抓取 HoYoLAB 官方星穹鐵道公告 RSS(主力來源,穩定可用)"""
    items = []
    try:
        resp = requests.get(HOYOLAB_FEED_URL, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        for entry in feed.entries:
            items.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
                "link": entry.get("link", ""),
                "source": "HoYoLAB(官方星穹鐵道公告)",
                "pub_date": entry.get("published", ""),
            })
    except Exception as e:
        print(f"[警告] 抓取 HoYoLAB 官方 RSS 失敗: {e}", file=sys.stderr)
    return items


def fetch_facebook_rss() -> list:
    """透過 RSSHub 把 Facebook 專頁轉成 RSS 讀取"""
    items = []
    feed_url = f"{RSSHUB_BASE}/facebook/page/{FB_PAGE_ID}"
    try:
        resp = requests.get(feed_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        for entry in feed.entries:
            items.append({
                "title": entry.get("title", ""),
                "summary": entry.get("summary", "") or entry.get("description", ""),
                "link": entry.get("link", ""),
                "source": "Facebook(崩壞：星穹鐵道 繁中專頁)",
                "pub_date": entry.get("published", ""),
            })
    except Exception as e:
        print(f"[警告] 抓取 Facebook RSS 失敗: {e}", file=sys.stderr)
    return items


def fetch_all() -> list:
    all_items = []
    all_items.extend(fetch_hoyolab_official_feed())  # 主力來源,穩定
    all_items.extend(fetch_facebook_rss())  # 輔助來源,best-effort
    return all_items


# ---------------------------------------------------------------------------
# Discord 推送
# ---------------------------------------------------------------------------

def send_discord(item: dict):
    if not DISCORD_WEBHOOK_URL:
        print("[錯誤] 未設定 DISCORD_WEBHOOK_URL,略過推送。以下為命中內容:")
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return

    embed = {
        "title": item["title"][:250] if item["title"] else "(無標題)",
        "url": item["link"],
        "description": trim_summary(item["summary"], 100),
        "color": 0x5865F2,
        "fields": [
            {"name": "來源", "value": item["source"], "inline": True},
            {"name": "發布時間", "value": item["pub_date"] or "未知", "inline": True},
        ],
    }
    payload = {
        "content": "🚨 偵測到星穹鐵道香港聯動相關消息！",
        "embeds": [embed],
    }
    try:
        resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[錯誤] 推送 Discord 失敗: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    sent_ids = load_sent_ids()
    all_items = fetch_all()
    print(f"共抓取 {len(all_items)} 筆資料")

    new_hits = 0
    for item in all_items:
        if not item.get("link"):
            continue
        item_id = make_id(item["link"])
        if item_id in sent_ids:
            continue  # 已處理過,略過(不論是否命中關鍵字都記錄避免重複判斷)

        sent_ids.add(item_id)

        combined_text = f"{item['title']} {item['summary']}"
        if is_hit(combined_text):
            print(f"[命中] {item['title']} | {item['link']}")
            send_discord(item)
            new_hits += 1

    save_sent_ids(sent_ids)
    print(f"完成。本次新增命中 {new_hits} 筆。")


if __name__ == "__main__":
    main()
