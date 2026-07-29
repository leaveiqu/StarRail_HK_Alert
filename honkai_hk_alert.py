# -*- coding: utf-8 -*-
"""
星穹鐵道 香港聯動消息 自動監控腳本
資料源:
  1. HoYoverse 國際服 啟動器資訊 API (data.post)
  2. 米哈遊 國服 啟動器資訊 API (data.post)  -> 代表米游社官方資訊
  3. Facebook 崩壞：星穹鐵道 繁中官方專頁 (透過 RSSHub 轉 RSS)

流程:抓取 -> 關鍵字比對 -> 與 sent_ids.json 去重 -> 推送 Discord -> 更新 sent_ids.json
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
]

SENT_IDS_FILE = Path(__file__).parent / "sent_ids.json"
MAX_KEEP_IDS = 800  # 避免檔案無限成長,只保留最近 N 筆

SOURCES_LAUNCHER = [
    {
        "name": "HoYoLAB(國際服官方資訊)",
        "url": "https://hkrpg-launcher-static.hoyoverse.com/hkrpg_global/mdk/launcher/api/content?language=zh-tw&launcher_id=35",
    },
    {
        "name": "米游社(國服官方資訊)",
        "url": "https://api-launcher.mihoyo.com/hkrpg_cn/mdk/launcher/api/content?language=zh-cn&launcher_id=33",
    },
]

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


def is_hit(text: str) -> bool:
    if not text:
        return False
    has_region = any(k in text for k in REGION_KEYWORDS)
    has_collab = any(k.lower() in text.lower() for k in COLLAB_KEYWORDS)
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

def fetch_launcher_source(source: dict) -> list:
    """抓取米哈遊啟動器 content API 的 post 區塊"""
    items = []
    try:
        resp = requests.get(source["url"], timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.json()
        posts = data.get("data", {}).get("post", []) or []
        for p in posts:
            title = p.get("title") or p.get("tittle") or ""
            link = p.get("url") or ""
            show_time = p.get("show_time", "")
            if not link:
                continue
            items.append({
                "title": title,
                "summary": "",  # 啟動器 API 不提供內文,摘要留空由 trim_summary 補預設字串
                "link": link,
                "source": source["name"],
                "pub_date": show_time,
            })
    except Exception as e:
        print(f"[警告] 抓取 {source['name']} 失敗: {e}", file=sys.stderr)
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
    for src in SOURCES_LAUNCHER:
        all_items.extend(fetch_launcher_source(src))
    all_items.extend(fetch_facebook_rss())
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
