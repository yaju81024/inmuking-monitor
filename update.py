#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INMU KING 動画モニター（独立ホスティング版）データ収集スクリプト。

GitHub Actions から15分ごとに実行され、data.json / history.json を更新する。
Python標準ライブラリのみで動作する（pip install 不要）。

環境変数:
  YOUTUBE_API_KEY  YouTube Data API v3 のキー。未設定の場合は YouTube を
                   スキップし、ニコニコだけで更新する（終了コードは 0）。

収集ルール:
  - タイトルに INMU KING / INMU_KING / INMUKING を含む動画だけを採用する
    （大小文字は区別しない）。タグ・説明文の一致は採用しない。
  - 既存 data.json の動画は保持し、新しく取得したもので上書き・追加する
    （検索の窓から漏れた過去動画を失わないため）。
  - 取得に失敗した場合は例外で終了し、ファイルは書き換えない。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_PATH = os.path.join(BASE_DIR, "data.json")
HISTORY_PATH = os.path.join(BASE_DIR, "history.json")

QUERIES = ["INMU KING", "INMU_KING", "INMUKING"]
TITLE_MARKERS = ["inmu king", "inmu_king", "inmuking"]
FEATURED_VIDEO_ID = "RkcI7ase0H0"  # 【MV】INMU KING（修正版）/ YAJU-MC
FEATURED_CHANNEL_URL = "https://www.youtube.com/channel/UCw6QbnRMUYY8lztr7lotahQ"
ORIGINAL_NICONICO_URL = "https://www.nicovideo.jp/watch/sm46647529"

NICONICO_API = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
USER_AGENT = "inmu-king-monitor/1.0 (+https://github.com/)"

JST = timezone(timedelta(hours=9))


def title_matches(title):
    t = (title or "").lower()
    return any(m in t for m in TITLE_MARKERS)


def http_get_json(url, params=None, headers=None, timeout=30):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def iso_to_ms(text):
    # "2026-08-10T00:00:00+09:00" や YouTube の "2026-11-08T11:24:15Z" に対応
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------- ニコニコ
def fetch_niconico():
    """スナップショット検索APIから対象動画を取得し、videoId -> 配列 の辞書を返す。"""
    fields = "contentId,title,userId,channelId,viewCounter,commentCounter,likeCounter,mylistCounter,startTime,thumbnailUrl"
    found = {}
    for q in QUERIES:
        for sort in ("-startTime", "-viewCounter"):
            params = {
                "q": q,
                "targets": "title",
                "fields": fields,
                "_sort": sort,
                "_limit": 100,
                "_offset": 0,
                "_context": "inmu-king-monitor",
            }
            res = http_get_json(NICONICO_API, params)
            for item in res.get("data", []):
                if not title_matches(item.get("title")):
                    continue
                vid = item["contentId"]
                found[vid] = [
                    "niconico",
                    vid,
                    item.get("title", ""),
                    None,  # スナップショットAPIは投稿者名を返さない
                    int(item.get("viewCounter", 0)),
                    int(item.get("likeCounter", 0)),
                    iso_to_ms(item["startTime"]),
                    item.get("thumbnailUrl", ""),
                    "https://www.nicovideo.jp/watch/" + vid,
                ]
    return found


# ---------------------------------------------------------------- YouTube
def fetch_youtube(api_key):
    """search.list + videos.list から対象動画を取得し、videoId -> 配列 の辞書を返す。"""
    ids = []
    seen = set()
    for q in QUERIES:
        for order in ("date", "viewCount"):
            params = {
                "part": "snippet",
                "q": q,
                "type": "video",
                "order": order,
                "maxResults": 50,
                "key": api_key,
            }
            res = http_get_json(YOUTUBE_API + "/search", params)
            for item in res.get("items", []):
                vid = item["id"]["videoId"]
                if vid not in seen:
                    seen.add(vid)
                    ids.append(vid)
    found = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        params = {
            "part": "snippet,statistics",
            "id": ",".join(chunk),
            "key": api_key,
        }
        res = http_get_json(YOUTUBE_API + "/videos", params)
        for item in res.get("items", []):
            snip = item.get("snippet", {})
            title = snip.get("title", "")
            if not title_matches(title):
                continue
            stats = item.get("statistics", {})
            vid = item["id"]
            found[vid] = [
                "youtube",
                vid,
                title,
                snip.get("channelTitle"),
                int(stats.get("viewCount", 0)),
                int(stats.get("likeCount", 0)),
                iso_to_ms(snip["publishedAt"]),
                "https://i.ytimg.com/vi/" + vid + "/hqdefault.jpg",
                "https://www.youtube.com/watch?v=" + vid,
            ]
    return found


def fetch_featured(api_key):
    """特設動画の現在値を videos.list で直接取得する。"""
    params = {
        "part": "snippet,statistics",
        "id": FEATURED_VIDEO_ID,
        "key": api_key,
    }
    res = http_get_json(YOUTUBE_API + "/videos", params)
    items = res.get("items", [])
    if not items:
        raise RuntimeError("featured video not found: " + FEATURED_VIDEO_ID)
    item = items[0]
    snip = item.get("snippet", {})
    stats = item.get("statistics", {})
    return {
        "videoId": FEATURED_VIDEO_ID,
        "title": snip.get("title", ""),
        "authorName": snip.get("channelTitle", ""),
        "channelUrl": FEATURED_CHANNEL_URL,
        "publishedAt": iso_to_ms(snip["publishedAt"]),
        "thumbnailUrl": "https://i.ytimg.com/vi/" + FEATURED_VIDEO_ID + "/maxresdefault.jpg",
        "watchUrl": "https://www.youtube.com/watch?v=" + FEATURED_VIDEO_ID,
        "viewCount": int(stats.get("viewCount", 0)),
        "likeCount": int(stats.get("likeCount", 0)),
        "commentCount": int(stats.get("commentCount", 0)),
        "description": snip.get("description", ""),
        "originalNiconicoUrl": ORIGINAL_NICONICO_URL,
    }


# ---------------------------------------------------------------- メイン
def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def main():
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()

    old_data = load_json(DATA_PATH, None)
    old_videos = {}
    if old_data:
        for row in old_data.get("videos", []):
            old_videos[row[0] + ":" + row[1]] = row
    history = load_json(HISTORY_PATH, [])

    merged = dict(old_videos)

    # --- ニコニコ（キー不要）。失敗したら例外のまま終了し、ファイルは壊さない。
    nico = fetch_niconico()
    for vid, row in nico.items():
        merged["niconico:" + vid] = row
    print("niconico fetched: %d" % len(nico))

    featured = old_data.get("featured") if old_data else None

    # --- YouTube（キーが無い／失敗した場合は警告して続行する）
    if not api_key:
        print("WARNING: YOUTUBE_API_KEY is not set; skipping YouTube.", file=sys.stderr)
    else:
        try:
            yt = fetch_youtube(api_key)
            for vid, row in yt.items():
                merged["youtube:" + vid] = row
            print("youtube fetched: %d" % len(yt))
            featured = fetch_featured(api_key)
            now_ms = int(time.time() * 1000)
            point = [now_ms, featured["viewCount"], featured["likeCount"], featured["commentCount"]]
            if not history or history[-1][1:] != point[1:]:
                history.append(point)
            print("featured: %d views, history points: %d" % (featured["viewCount"], len(history)))
        except urllib.error.HTTPError as e:
            # クォータ枯渇（403/429）など。ニコニコ分だけでも更新する。
            print("WARNING: YouTube fetch failed (HTTP %s); keeping previous YouTube data." % e.code, file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print("WARNING: YouTube fetch failed (%s); keeping previous YouTube data." % e, file=sys.stderr)

    rows = sorted(merged.values(), key=lambda r: r[4], reverse=True)  # 人気順
    new_keys = sorted(set(merged.keys()) - set(old_videos.keys()))

    now = datetime.now(JST)
    data = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M JST"),
        "updatedAtMs": int(now.timestamp() * 1000),
        "total": len(rows),
        "youtube": sum(1 for r in rows if r[0] == "youtube"),
        "niconico": sum(1 for r in rows if r[0] == "niconico"),
        "newKeys": new_keys,
        "featured": featured,
        "videos": rows,
    }
    save_json_atomic(DATA_PATH, data)
    save_json_atomic(HISTORY_PATH, history)
    print("saved: total=%d (youtube=%d, niconico=%d), new=%d" % (data["total"], data["youtube"], data["niconico"], len(new_keys)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
