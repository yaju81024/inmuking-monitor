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
  - YouTube検索はクォータ節約のため間引く: 新着検索は約1時間に1回、
    人気検索は約6時間に1回。特設の直接取得とニコニコ取得は毎回行う。
  - YouTube取得に失敗した場合は警告して前回データを保持し、ニコニコ分
    だけでも更新する。ニコニコ取得の失敗時は例外で終了し、ファイルは
    書き換えない。
  - 手動エントリ（例外）: 削除済みの本家YouTube版（MFwtpM21wWc）は作者が
    公表した確定値で固定登録する。タイトルに曲名を含まない派生
    （1vnTy_ll7Aw）はID指定で毎回直接取得して数値を更新する。
    行の10番目の要素に "deleted" / "manual" の印を付ける。
  - 保存データが古いまま残らないよう、取得時刻（fetchedAt）を動画
    ごとに記録し、毎回いちばん古いものから最大50件を videos.list で
    再取得して値を更新する（ローリング再取得）。再取得で返ってこな
    かった動画は、削除・非公開になった可能性があるものとして、最後に
    確認できた値のまま「gone」の印を付けて凍結し、一覧に残す（ユーザー
    の方針: 消えた動画も記録として残す）。凍結した動画は再取得の対象
    から外れるが、通常の検索で再び取得されれば印は外れて最新化される。
    特設・手動ウォッチ・本家は対象外（特設と手動は毎回取得、本家は
    確定値で固定のため）。
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

# 手動エントリ: 削除済みの本家YouTube版。数値は作者やじゅまん本人が
# YouTubeコミュニティ投稿で明かした削除時点の確定値（2026-11-11確認）。
# コメント数の最終値は非公表のため一覧には含めない（行は再生数・高評価まで）。
ORIGINAL_YOUTUBE_ID = "MFwtpM21wWc"
ORIGINAL_YOUTUBE_ROW = [
    "youtube",
    ORIGINAL_YOUTUBE_ID,
    "【MV】INMU KING / YAJU-MC",
    "やじゅまん",
    28742048,
    347592,
    1786287600000,  # 2026-08-10 00:00 JST
    "https://nicovideo.cdn.nimg.jp/thumbnails/46647529/46647529.14830621",
    "https://www.youtube.com/watch?v=" + ORIGINAL_YOUTUBE_ID,
    "deleted",
]

# 手動エントリ: タイトルに曲名を含まないため通常収集で拾えない派生。
# チバニャン「お前嘘臭ぇんだよスギ」（溝口勇児モノマネ）。毎回ID指定で
# 直接取得して数値を更新する。取得できない間は前回値（初回はシード値）を保持。
MANUAL_WATCH_IDS = ["1vnTy_ll7Aw"]
MANUAL_SEED_ROWS = {
    "1vnTy_ll7Aw": [
        "youtube",
        "1vnTy_ll7Aw",
        "お前嘘臭ぇんだよスギ",
        "チバニャンの波動チャンネル",
        5094891,
        23936,
        1787238000000,  # 2026-08-21 JST（直接取得で正確な値に上書きされる）
        "https://i.ytimg.com/vi/1vnTy_ll7Aw/hqdefault.jpg",
        "https://www.youtube.com/watch?v=1vnTy_ll7Aw",
        "manual",
    ],
}

NICONICO_API = "https://snapshot.search.nicovideo.jp/api/v2/snapshot/video/contents/search"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
USER_AGENT = "inmu-king-monitor/1.0 (+https://github.com/)"

JST = timezone(timedelta(hours=9))

# YouTube検索の間引き間隔（クォータ節約）。検索は1回100ユニットと重いため、
# 毎回は実行しない。特設の直接取得（1ユニット）とニコニコ取得は毎回行う。
SEARCH_INTERVAL_MS = 60 * 60 * 1000        # 新着検索は約1時間に1回
POPULAR_INTERVAL_MS = 6 * 60 * 60 * 1000   # 人気検索は約6時間に1回
REFRESH_BATCH = 50                         # ローリング再取得は毎回最大50件（videos.list 1回分）


def title_matches(title):
    t = (title or "").lower()
    return any(m in t for m in TITLE_MARKERS)


def http_get_json(url, params=None, headers=None, timeout=30):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def http_get_json_youtube(url, params=None, attempts=4):
    """YouTube API 専用: 429（レート制限）のときは間隔を空けて再試行する。

    GitHub Actions の共有IPからは検索系が 429 を返しやすい。数十秒空けると
    通ることがあるため、指数バックオフで最大 attempts 回まで試す。
    """
    last_err = None
    for i in range(attempts):
        try:
            return http_get_json(url, params)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code != 429 or i == attempts - 1:
                raise
            wait = 20 * (2 ** i)
            print("YouTube HTTP 429; retrying in %ds..." % wait, flush=True)
            time.sleep(wait)
    raise last_err


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
def fetch_youtube(api_key, orders):
    """search.list + videos.list から対象動画を取得し、videoId -> 配列 の辞書を返す。

    orders は ("date", "viewCount") のうち今回実行する分だけを渡す
    （クォータ節約のため呼び出し側で間引く）。
    """
    ids = []
    seen = set()
    for q in QUERIES:
        for order in orders:
            params = {
                "part": "snippet",
                "q": q,
                "type": "video",
                "order": order,
                "maxResults": 50,
                "key": api_key,
            }
            res = http_get_json_youtube(YOUTUBE_API + "/search", params)
            for item in res.get("items", []):
                vid = item["id"]["videoId"]
                if vid not in seen:
                    seen.add(vid)
                    ids.append(vid)
            time.sleep(2)  # 検索を連続で叩かない（レート制限対策）
    found = {}
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        params = {
            "part": "snippet,statistics",
            "id": ",".join(chunk),
            "key": api_key,
        }
        res = http_get_json_youtube(YOUTUBE_API + "/videos", params)
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
    res = http_get_json_youtube(YOUTUBE_API + "/videos", params)
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


def fetch_manual(api_key):
    """手動ウォッチリストの動画を videos.list で直接取得する。

    タイトル一致チェックは適用しない（タイトルに曲名を含まない派生が対象）。
    返り値は videoId -> 配列（10番目の要素に "manual" の印）。
    """
    found = {}
    if not MANUAL_WATCH_IDS:
        return found
    params = {
        "part": "snippet,statistics",
        "id": ",".join(MANUAL_WATCH_IDS),
        "key": api_key,
    }
    res = http_get_json_youtube(YOUTUBE_API + "/videos", params)
    for item in res.get("items", []):
        snip = item.get("snippet", {})
        stats = item.get("statistics", {})
        vid = item["id"]
        found[vid] = [
            "youtube",
            vid,
            snip.get("title", ""),
            snip.get("channelTitle"),
            int(stats.get("viewCount", 0)),
            int(stats.get("likeCount", 0)),
            iso_to_ms(snip["publishedAt"]),
            "https://i.ytimg.com/vi/" + vid + "/hqdefault.jpg",
            "https://www.youtube.com/watch?v=" + vid,
            "manual",
        ]
    return found


def refresh_stale_youtube(api_key, merged, fetched_at, now_ms):
    """取得時刻が古い YouTube 動画から最大 REFRESH_BATCH 件を再取得する。

    merged（"youtube:<id>" -> 行）と fetched_at（id -> 取得時刻ms）を直接更新し、
    (更新件数, 凍結件数) を返す。再取得で返ってこなかった動画は削除・非公開に
    なった可能性があるものとして、行に "gone" の印を付けて凍結し、一覧に
    残す（消さない）。凍結済みの動画は候補から外す。呼び出し側で例外を
    捕捉すること。
    """
    skip = {ORIGINAL_YOUTUBE_ID, FEATURED_VIDEO_ID} | set(MANUAL_WATCH_IDS)
    candidates = []
    for key, row in merged.items():
        if row[0] != "youtube" or row[1] in skip:
            continue
        if len(row) > 9 and row[9] == "gone":
            continue  # 凍結済みは再取得しない
        candidates.append((int(fetched_at.get(row[1], 0)), row[1]))
    if not candidates:
        return (0, 0)
    candidates.sort()
    targets = [vid for _, vid in candidates[:REFRESH_BATCH]]
    params = {
        "part": "snippet,statistics",
        "id": ",".join(targets),
        "key": api_key,
    }
    res = http_get_json_youtube(YOUTUBE_API + "/videos", params)
    returned = set()
    for item in res.get("items", []):
        snip = item.get("snippet", {})
        stats = item.get("statistics", {})
        vid = item["id"]
        returned.add(vid)
        old = merged.get("youtube:" + vid)
        row = [
            "youtube",
            vid,
            snip.get("title", ""),
            snip.get("channelTitle"),
            int(stats.get("viewCount", 0)),
            int(stats.get("likeCount", 0)),
            iso_to_ms(snip["publishedAt"]),
            "https://i.ytimg.com/vi/" + vid + "/hqdefault.jpg",
            "https://www.youtube.com/watch?v=" + vid,
        ]
        if old is not None and len(old) > 9:
            row.append(old[9])  # 印（フラグ）があれば引き継ぐ
        merged["youtube:" + vid] = row
        fetched_at[vid] = now_ms
    frozen = 0
    for vid in targets:
        if vid not in returned:
            row = merged.get("youtube:" + vid)
            if row is not None:
                if len(row) > 9:
                    row[9] = "gone"
                else:
                    row.append("gone")
                frozen += 1
    return (len(returned), frozen)


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
    last_search_ms = int(old_data.get("lastSearchMs", 0)) if old_data else 0
    last_popular_ms = int(old_data.get("lastPopularMs", 0)) if old_data else 0
    fetched_at = {}
    if old_data:
        for k, v in (old_data.get("fetchedAt") or {}).items():
            try:
                fetched_at[str(k)] = int(v)
            except (TypeError, ValueError):
                pass

    # --- YouTube（キーが無い／失敗した場合は警告して続行する）
    if not api_key:
        print("WARNING: YOUTUBE_API_KEY is not set; skipping YouTube.", file=sys.stderr)
    else:
        now_ms = int(time.time() * 1000)
        orders = []
        if now_ms - last_search_ms >= SEARCH_INTERVAL_MS:
            orders.append("date")
        if now_ms - last_popular_ms >= POPULAR_INTERVAL_MS:
            orders.append("viewCount")
        if orders:
            try:
                yt = fetch_youtube(api_key, orders)
                for vid, row in yt.items():
                    merged["youtube:" + vid] = row
                    fetched_at[vid] = now_ms
                print("youtube fetched: %d (orders: %s)" % (len(yt), ",".join(orders)))
                if "date" in orders:
                    last_search_ms = now_ms
                if "viewCount" in orders:
                    last_popular_ms = now_ms
            except urllib.error.HTTPError as e:
                # クォータ枯渇（403/429）など。ニコニコ分だけでも更新する。
                print("WARNING: YouTube search failed (HTTP %s); keeping previous YouTube data." % e.code, file=sys.stderr)
            except Exception as e:  # noqa: BLE001
                print("WARNING: YouTube search failed (%s); keeping previous YouTube data." % e, file=sys.stderr)
        else:
            print("youtube search skipped (interval not reached)")
        # 特設は毎回直接取得する（1ユニット）。
        try:
            featured = fetch_featured(api_key)
            point = [now_ms, featured["viewCount"], featured["likeCount"], featured["commentCount"]]
            if not history or history[-1][1:] != point[1:]:
                history.append(point)
            print("featured: %d views, history points: %d" % (featured["viewCount"], len(history)))
            if FEATURED_VIDEO_ID in merged:
                fetched_at[FEATURED_VIDEO_ID] = now_ms
        except urllib.error.HTTPError as e:
            print("WARNING: featured fetch failed (HTTP %s); keeping previous featured." % e.code, file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print("WARNING: featured fetch failed (%s); keeping previous featured." % e, file=sys.stderr)
        # 手動ウォッチリストは毎回直接取得する（1ユニット）。
        try:
            manual = fetch_manual(api_key)
            for vid, row in manual.items():
                merged["youtube:" + vid] = row
                fetched_at[vid] = now_ms
            print("manual watch fetched: %d" % len(manual))
        except urllib.error.HTTPError as e:
            print("WARNING: manual fetch failed (HTTP %s); keeping previous manual entries." % e.code, file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print("WARNING: manual fetch failed (%s); keeping previous manual entries." % e, file=sys.stderr)
        # ローリング再取得: 取得時刻が古いものから最大50件を取り直す（1ユニット）。
        try:
            refreshed, frozen = refresh_stale_youtube(api_key, merged, fetched_at, now_ms)
            print("rolling refresh: %d updated, %d frozen" % (refreshed, frozen))
        except urllib.error.HTTPError as e:
            print("WARNING: rolling refresh failed (HTTP %s); keeping previous data." % e.code, file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print("WARNING: rolling refresh failed (%s); keeping previous data." % e, file=sys.stderr)

    # 手動エントリの保証: 本家は確定値で常時登録し、ウォッチ分は未登録なら
    # シード値で登録する（キー未設定・取得失敗時でも一覧から消えない）。
    merged["youtube:" + ORIGINAL_YOUTUBE_ID] = list(ORIGINAL_YOUTUBE_ROW)
    for vid, seed in MANUAL_SEED_ROWS.items():
        merged.setdefault("youtube:" + vid, list(seed))

    # fetchedAt は現存する YouTube 動画分だけに絞る（肥大化防止）。
    live_yt_ids = {r[1] for r in merged.values() if r[0] == "youtube"}
    fetched_at = {k: v for k, v in fetched_at.items() if k in live_yt_ids}

    rows = sorted(merged.values(), key=lambda r: r[4], reverse=True)  # 人気順
    new_keys = sorted(set(merged.keys()) - set(old_videos.keys()))
    # 手動エントリは「新着」として扱わない。
    new_keys = [k for k in new_keys
                if k != "youtube:" + ORIGINAL_YOUTUBE_ID
                and k not in ("youtube:" + v for v in MANUAL_WATCH_IDS)]

    now = datetime.now(JST)
    data = {
        "updatedAt": now.strftime("%Y-%m-%d %H:%M JST"),
        "updatedAtMs": int(now.timestamp() * 1000),
        "total": len(rows),
        "youtube": sum(1 for r in rows if r[0] == "youtube"),
        "niconico": sum(1 for r in rows if r[0] == "niconico"),
        "newKeys": new_keys,
        "lastSearchMs": last_search_ms,
        "lastPopularMs": last_popular_ms,
        "fetchedAt": fetched_at,
        "featured": featured,
        "videos": rows,
    }
    save_json_atomic(DATA_PATH, data)
    save_json_atomic(HISTORY_PATH, history)
    print("saved: total=%d (youtube=%d, niconico=%d), new=%d" % (data["total"], data["youtube"], data["niconico"], len(new_keys)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
