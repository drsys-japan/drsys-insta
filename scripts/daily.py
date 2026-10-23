"""毎日のカルーセル投稿を準備・投稿するスクリプト（GitHub Actions から呼ぶ）。

    python scripts/daily.py prepare [--date YYYY-MM-DD]
        posts/<日付>.json があれば、表現チェックをしてから JPEG 画像を public/<日付>/ に作る。
    python scripts/daily.py publish --date YYYY-MM-DD [--no-wait]
        予定時刻まで待ってから、Instagram API でカルーセルを投稿する。
    python scripts/daily.py refresh-token
        投稿用の鍵（長期アクセストークン）の有効期限を延長する。

投稿用の鍵は環境変数 IG_ACCESS_TOKEN で渡す（GitHub の Secrets に保存）。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "posts"
PUBLIC = ROOT / "public"
JST = timezone(timedelta(hours=9))
API = f"https://graph.instagram.com/{os.environ.get('IG_API_VERSION', 'v26.0')}"

# 化粧品として使えない表現（見つかったら投稿しない）
NG_PATTERNS = [
    r"治る", r"治す", r"治り", r"消える", r"消す", r"薄くなる", r"若返", r"改善",
    r"小顔", r"輪郭形成", r"脂肪", r"リフトアップ", r"引き上げ",
    r"細胞[にをが]", r"細胞レベル", r"活性化", r"再生", r"生まれ変わ", r"真皮",
    r"炎症", r"抜け毛が減", r"抜け毛を減", r"生えて", r"生える", r"育毛", r"発毛",
    r"絶対", r"[1一]回で変わ", r"稼げ", r"稼ぐ", r"月\s*\d+\s*万", r"誰でも簡単", r"不労所得",
]


def today_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d")


def post_path(date: str) -> Path:
    return POSTS / f"{date}.json"


def load(date: str) -> dict | None:
    p = post_path(date)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save(date: str, data: dict) -> None:
    post_path(date).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def gh_output(**kv) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            for k, v in kv.items():
                f.write(f"{k}={v}\n")
    for k, v in kv.items():
        print(f"{k}={v}")


def gh_summary(text: str) -> None:
    out = os.environ.get("GITHUB_STEP_SUMMARY")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


def all_text(data: dict) -> str:
    parts = [data.get("caption", "")]
    for s in data.get("slides", []):
        parts += [str(v) for k, v in s.items() if k != "type"]
    return "\n".join(parts)


def ng_check(data: dict) -> list[str]:
    text = all_text(data)
    hits = []
    for pat in NG_PATTERNS:
        for m in re.finditer(pat, text):
            start = max(0, m.start() - 12)
            hits.append(f"「{m.group(0)}」… {text[start:m.end() + 12].replace(chr(10), ' ')}")
    return hits


def raw_url(date: str, name: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "drsys-japan/drsys-insta")
    return f"https://raw.githubusercontent.com/{repo}/main/public/{date}/{name}"


# ---------------------------------------------------------------- prepare

def cmd_prepare(date: str) -> int:
    data = load(date)
    if data is None:
        gh_summary(f"{date}: 投稿データ（posts/{date}.json）がないので、今日は投稿しません。")
        gh_output(skip="true", date=date)
        return 0
    if data.get("type") != "carousel":
        gh_summary(f"{date}: カルーセルの日ではないのでスキップします（type={data.get('type')}）。")
        gh_output(skip="true", date=date)
        return 0
    if data.get("status") in ("prepared", "published"):
        gh_summary(f"{date}: すでに準備済み（status={data['status']}）なのでスキップします。")
        gh_output(skip="true", date=date)
        return 0

    hits = ng_check(data)
    if hits:
        gh_summary("## 使えない表現が見つかったので、投稿を止めました\n\n" + "\n".join(f"- {h}" for h in hits))
        data["status"] = "blocked"
        data["blocked_reason"] = hits
        save(date, data)
        gh_output(skip="true", date=date, blocked="true")
        return 1  # 失敗扱いにして、GitHub からメールで知らせる

    sys.path.insert(0, str(ROOT / "carousel"))
    from render import render  # noqa: E402

    out = PUBLIC / date
    paths = render(str(post_path(date)), str(out), "jpg")
    data["status"] = "prepared"
    data["images"] = [p.name for p in paths]
    save(date, data)

    lines = [f"## {date} のカルーセルを準備しました（{data.get('post_at', '12:00')} 投稿予定）", ""]
    lines += [f"![slide]({raw_url(date, p.name)})" for p in paths]
    lines += ["", "### キャプション", "", "```", data.get("caption", ""), "```",
              "", "問題なければ、このページ上の「Review deployments」から承認してください。"]
    gh_summary("\n".join(lines))
    gh_output(skip="false", date=date, post_at=data.get("post_at", "12:00"))
    return 0


# ---------------------------------------------------------------- Instagram API

def api(method: str, path: str, params: dict) -> dict:
    token = os.environ["IG_ACCESS_TOKEN"]
    params = {**params, "access_token": token}
    url = f"{API}/{path.lstrip('/')}"
    body = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=body, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        detail = detail.replace(token, "***")
        raise RuntimeError(f"Instagram API エラー {e.code} ({path}): {detail}") from None


def wait_status(container_id: str, label: str) -> None:
    for _ in range(20):  # 最大およそ5分
        st = api("GET", container_id, {"fields": "status_code,status"})
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"{label} の処理に失敗しました: {st}")
        time.sleep(15)
    raise RuntimeError(f"{label} の処理が時間内に終わりませんでした")


def wait_url(url: str) -> None:
    for _ in range(20):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30) as r:
                if r.status == 200 and "image" in r.headers.get("Content-Type", ""):
                    return
        except urllib.error.URLError:
            pass
        time.sleep(15)
    raise RuntimeError(f"画像が公開URLで見つかりません: {url}")


def wait_until(post_at: str, date: str) -> None:
    hh, mm = (int(x) for x in post_at.split(":"))
    target = datetime.strptime(date, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=JST)
    delay = (target - datetime.now(JST)).total_seconds()
    if delay > 0:
        delay = min(delay, 5.5 * 3600)  # GitHub Actions の上限（6時間）に収める
        print(f"{post_at} まで {int(delay // 60)} 分待ちます")
        time.sleep(delay)


def cmd_publish(date: str, no_wait: bool) -> int:
    data = load(date)
    if not data or data.get("status") != "prepared":
        print(f"{date}: 準備済みの投稿がありません（status={data and data.get('status')}）")
        return 1
    if not no_wait:
        wait_until(data.get("post_at", "12:00"), date)

    me = api("GET", "me", {"fields": "user_id,username"})
    ig_id = me["user_id"]
    print(f"投稿先: @{me.get('username')}")

    urls = [raw_url(date, name) for name in data["images"]]
    for u in urls:
        wait_url(u)

    children = []
    for i, u in enumerate(urls, 1):
        c = api("POST", f"{ig_id}/media", {"image_url": u, "is_carousel_item": "true"})
        wait_status(c["id"], f"{i}枚目の画像")
        children.append(c["id"])

    carousel = api("POST", f"{ig_id}/media", {
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "caption": data.get("caption", ""),
    })
    wait_status(carousel["id"], "カルーセル")
    published = api("POST", f"{ig_id}/media_publish", {"creation_id": carousel["id"]})
    media = api("GET", published["id"], {"fields": "permalink,timestamp"})

    data["status"] = "published"
    data["media_id"] = published["id"]
    data["permalink"] = media.get("permalink")
    data["published_at"] = media.get("timestamp")
    save(date, data)
    gh_summary(f"## 投稿しました\n\n{media.get('permalink')}")
    return 0


# ---------------------------------------------------------------- token refresh

def cmd_refresh() -> int:
    token = os.environ["IG_ACCESS_TOKEN"]
    url = "https://graph.instagram.com/refresh_access_token?" + urllib.parse.urlencode(
        {"grant_type": "ig_refresh_token", "access_token": token})
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            res = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print("鍵の延長に失敗しました:", e.read().decode(errors="replace").replace(token, "***"))
        return 1
    days = int(res.get("expires_in", 0)) // 86400
    new = res.get("access_token", "")
    if new and new != token:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            print(f"::add-mask::{new}")
            with open(out, "a") as f:
                f.write(f"new_token={new}\n")
        gh_summary(f"鍵を更新しました（あと約{days}日有効）。新しい鍵を Secrets に保存します。")
    else:
        gh_summary(f"鍵の有効期限を延長しました（あと約{days}日有効）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["prepare", "publish", "refresh-token"])
    ap.add_argument("--date", default=today_jst())
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()
    if a.command == "prepare":
        return cmd_prepare(a.date)
    if a.command == "publish":
        return cmd_publish(a.date, a.no_wait)
    return cmd_refresh()


if __name__ == "__main__":
    sys.exit(main())
