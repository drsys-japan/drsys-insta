"""Threads と X（旧Twitter）への投稿スクリプト（GitHub Actions から呼ぶ）。

    python scripts/social.py threads --date YYYY-MM-DD
        posts/<日付>.json の "threads" の文章を、表紙の画像を添えて Threads に投稿する。
    python scripts/social.py x --date YYYY-MM-DD
        posts/<日付>.json の "x" の文章を X に投稿する（文字だけ）。
    python scripts/social.py refresh-threads
        Threads 用の鍵（長期アクセストークン）の有効期限を延長する。
    python scripts/social.py check
        投稿はせず、Threads と X の鍵が使えるかだけ確かめる。

Instagram の投稿のあとに呼ぶ（承認は Instagram と共通）。予定時刻より前なら、その時刻まで待つ。
鍵が Secrets に入っていない、または投稿データに文章がない場合は、何もせずに終わる。

鍵は環境変数で渡す（GitHub の Secrets に保存）。
    Threads: THREADS_ACCESS_TOKEN
    X:       X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from daily import gh_summary, load, ng_text, raw_url, save, today_jst, wait_until  # noqa: E402

THREADS_API = "https://graph.threads.net/v1.0"
X_API = "https://api.x.com/2/tweets"
THREADS_MAX = 500   # Threads の文字数の上限
X_MAX = 280         # X の上限（日本語は1文字=2として数える）


def request(req: urllib.request.Request, secret_words: list[str], label: str) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        for w in secret_words:
            if w:
                detail = detail.replace(w, "***")
        raise RuntimeError(f"{label} エラー {e.code}: {detail}") from None


# ---------------------------------------------------------------- Threads

def threads_api(method: str, path: str, params: dict) -> dict:
    token = os.environ["THREADS_ACCESS_TOKEN"]
    params = {**params, "access_token": token}
    url = f"{THREADS_API}/{path.lstrip('/')}"
    body = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        body = urllib.parse.urlencode(params).encode()
    return request(urllib.request.Request(url, data=body, method=method), [token], f"Threads API ({path})")


def threads_wait(container_id: str) -> None:
    for _ in range(20):  # 最大およそ5分
        st = threads_api("GET", container_id, {"fields": "status,error_message"})
        if st.get("status") == "FINISHED":
            return
        if st.get("status") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Threads の投稿準備に失敗しました: {st}")
        time.sleep(15)
    raise RuntimeError("Threads の投稿準備が時間内に終わりませんでした")


def cmd_threads(date: str, no_wait: bool = False) -> int:
    data = load(date)
    text = (data or {}).get("threads", "").strip()
    if not os.environ.get("THREADS_ACCESS_TOKEN"):
        gh_summary("Threads: 鍵（THREADS_ACCESS_TOKEN）が未設定なので、投稿しませんでした。")
        return 0
    if not text:
        gh_summary(f"Threads: {date} の投稿データに文章（threads）がないので、投稿しませんでした。")
        return 0
    if data.get("threads_status") == "published":
        gh_summary(f"Threads: すでに投稿済みです（{data.get('threads_permalink')}）")
        return 0
    if len(text) > THREADS_MAX:
        gh_summary(f"Threads: 文章が{len(text)}文字で、上限の{THREADS_MAX}文字を超えているので投稿しませんでした。")
        return 1
    if hits := ng_text(text):
        gh_summary("Threads: 使えない表現があったので投稿しませんでした\n\n" + "\n".join(f"- {h}" for h in hits))
        return 1

    if not no_wait:
        wait_until(data.get("post_at", "12:00"), date)
    params = {"media_type": "TEXT", "text": text}
    if data.get("images"):  # 表紙の画像を1枚添える
        params = {"media_type": "IMAGE", "image_url": raw_url(date, data["images"][0]), "text": text}
    container = threads_api("POST", "me/threads", params)
    threads_wait(container["id"])
    published = threads_api("POST", "me/threads_publish", {"creation_id": container["id"]})
    post = threads_api("GET", published["id"], {"fields": "permalink"})

    data = load(date)  # Instagram 側の書き込みを消さないように読み直す
    data["threads_status"] = "published"
    data["threads_id"] = published["id"]
    data["threads_permalink"] = post.get("permalink")
    save(date, data)
    gh_summary(f"## Threads に投稿しました\n\n{post.get('permalink')}")
    return 0


def cmd_refresh_threads() -> int:
    token = os.environ["THREADS_ACCESS_TOKEN"]
    url = "https://graph.threads.net/refresh_access_token?" + urllib.parse.urlencode(
        {"grant_type": "th_refresh_token", "access_token": token})
    try:
        res = request(urllib.request.Request(url), [token], "Threads の鍵の延長")
    except RuntimeError as e:
        print(e)
        return 1
    days = int(res.get("expires_in", 0)) // 86400
    new = res.get("access_token", "")
    if new and new != token:
        print(f"::add-mask::{new}")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"new_token={new}\n")
        gh_summary(f"Threads の鍵を更新しました（あと約{days}日有効）。新しい鍵を Secrets に保存します。")
    else:
        gh_summary(f"Threads の鍵の有効期限を延長しました（あと約{days}日有効）。")
    return 0


# ---------------------------------------------------------------- X

def x_length(text: str) -> int:
    """X の数え方（英数字は1、日本語や絵文字は2）。"""
    n = 0
    for ch in text:
        c = ord(ch)
        light = c <= 0x10FF or 0x2000 <= c <= 0x200D or 0x2010 <= c <= 0x201F or 0x2032 <= c <= 0x2037
        n += 1 if light else 2
    return n


def x_keys() -> tuple[str, str, str, str]:
    return (os.environ.get("X_API_KEY", ""), os.environ.get("X_API_SECRET", ""),
            os.environ.get("X_ACCESS_TOKEN", ""), os.environ.get("X_ACCESS_TOKEN_SECRET", ""))


def x_auth_header(method: str, url: str) -> str:
    """OAuth 1.0a の署名（本文が JSON のときは、署名にはクエリと oauth_* だけを使う）。"""
    key, key_secret, token, token_secret = x_keys()
    q = lambda s: urllib.parse.quote(s, safe="~")  # noqa: E731
    oauth = {
        "oauth_consumer_key": key,
        "oauth_nonce": secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    base_url, _, query = url.partition("?")
    params = list(oauth.items()) + urllib.parse.parse_qsl(query)
    param_str = "&".join(f"{k}={v}" for k, v in sorted((q(k), q(v)) for k, v in params))
    base = "&".join([method.upper(), q(base_url), q(param_str)])
    signing_key = f"{q(key_secret)}&{q(token_secret)}".encode()
    oauth["oauth_signature"] = base64.b64encode(hmac.new(signing_key, base.encode(), hashlib.sha1).digest()).decode()
    return "OAuth " + ", ".join(f'{q(k)}="{q(v)}"' for k, v in sorted(oauth.items()))


def x_api(method: str, url: str, payload: dict | None = None) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", x_auth_header(method, url))
    if body is not None:
        req.add_header("Content-Type", "application/json")
    return request(req, list(x_keys()), "X API")


def cmd_x(date: str, no_wait: bool = False) -> int:
    data = load(date)
    text = (data or {}).get("x", "").strip()
    if not all(x_keys()):
        gh_summary("X: 鍵（X_API_KEY ほか4つ）が未設定なので、投稿しませんでした。")
        return 0
    if not text:
        gh_summary(f"X: {date} の投稿データに文章（x）がないので、投稿しませんでした。")
        return 0
    if data.get("x_status") == "published":
        gh_summary(f"X: すでに投稿済みです（{data.get('x_url')}）")
        return 0
    if x_length(text) > X_MAX:
        gh_summary(f"X: 文章が長すぎるので投稿しませんでした（{x_length(text)}／{X_MAX}。日本語は1文字=2で数えます）。")
        return 1
    if hits := ng_text(text):
        gh_summary("X: 使えない表現があったので投稿しませんでした\n\n" + "\n".join(f"- {h}" for h in hits))
        return 1

    if not no_wait:
        wait_until(data.get("post_at", "12:00"), date)
    res = x_api("POST", X_API, {"text": text})
    tweet_id = res["data"]["id"]
    url = f"https://x.com/i/web/status/{tweet_id}"

    data = load(date)
    data["x_status"] = "published"
    data["x_id"] = tweet_id
    data["x_url"] = url
    save(date, data)
    gh_summary(f"## X に投稿しました\n\n{url}")
    return 0


# ---------------------------------------------------------------- check

def cmd_check() -> int:
    lines = ["## Threads と X の接続テスト", ""]
    if os.environ.get("THREADS_ACCESS_TOKEN"):
        try:
            me = threads_api("GET", "me", {"fields": "id,username"})
            lines.append(f"- Threads: OK（@{me.get('username')}）")
        except RuntimeError as e:
            lines.append(f"- Threads: NG … {e}")
    else:
        lines.append("- Threads: 鍵が未設定")
    if all(x_keys()):
        try:
            me = x_api("GET", "https://api.x.com/2/users/me")
            lines.append(f"- X: OK（@{me.get('data', {}).get('username')}）")
        except RuntimeError as e:
            lines.append(f"- X: NG … {e}")
    else:
        lines.append("- X: 鍵が未設定")
    gh_summary("\n".join(lines))
    return 1 if any("NG" in s for s in lines) else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["threads", "x", "refresh-threads", "check"])
    ap.add_argument("--date", default=today_jst())
    ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args()
    if a.command == "threads":
        return cmd_threads(a.date, a.no_wait)
    if a.command == "x":
        return cmd_x(a.date, a.no_wait)
    if a.command == "refresh-threads":
        return cmd_refresh_threads()
    return cmd_check()


if __name__ == "__main__":
    sys.exit(main())
