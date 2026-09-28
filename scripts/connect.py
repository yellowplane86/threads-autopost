"""Threads接続ツール。認可URLの生成・トークン交換・接続確認を行う。

Threads APIは管理画面から直接トークンを発行できず、OAuth 2.0 の認可フローが必須。
自分のアカウントに自分で投稿するだけの用途でも同じ手順を通る必要がある。

--- 使う順番 ---

1) 認可URLを作る（ブラウザで開く）
   python scripts/connect.py authurl --app-id <THREADS_APP_ID> \
       --redirect-uri https://localhost/callback

2) 承認後に戻ってきたURLから code= を取り出し、短期トークンに交換
   python scripts/connect.py exchange --app-id <ID> --app-secret <SECRET> \
       --redirect-uri https://localhost/callback --code <CODE>

3) 長期トークン（60日）に交換
   python scripts/connect.py longlived --app-secret <SECRET> --token <SHORT_LIVED>

4) 接続確認（公開投稿は一切しない）
   python scripts/connect.py check --user-id <ID> --token <LONG_LIVED>

check は環境変数 THREADS_USER_ID / THREADS_ACCESS_TOKEN も読むので、
GitHub Actions からは引数なしで実行できる。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

AUTH_HOSTS = ("threads.com", "threads.net")
GRAPH_HOSTS = ("graph.threads.com", "graph.threads.net")
SCOPES = ["threads_basic", "threads_content_publish"]
UA = "nissin-travel-autopost/1.0"


def _req(url: str, params: dict[str, str], method: str = "GET") -> tuple[int, dict | str]:
    data = urllib.parse.urlencode(params)
    if method == "GET":
        full, body = f"{url}?{data}", None
    else:
        full, body = url, data.encode()
    req = urllib.request.Request(
        full, data=body, method=method, headers={"User-Agent": UA}
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            raw = r.read().decode(errors="replace")
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw[:400]
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw[:400]
    except urllib.error.URLError as e:
        return 0, f"network error: {e.reason}"


def _err(payload: dict | str) -> str:
    if isinstance(payload, dict):
        e = payload.get("error") or {}
        if isinstance(e, dict) and e:
            return f"{e.get('type', '?')}: {e.get('message', payload)}"
        return json.dumps(payload, ensure_ascii=False)[:300]
    return str(payload)[:300]


# --- サブコマンド --------------------------------------------------------


def cmd_authurl(a: argparse.Namespace) -> int:
    scope = ",".join(SCOPES)
    for host in AUTH_HOSTS:
        q = urllib.parse.urlencode(
            {
                "client_id": a.app_id,
                "redirect_uri": a.redirect_uri,
                "scope": scope,
                "response_type": "code",
            }
        )
        print(f"\n# {host} 版")
        print(f"https://{host}/oauth/authorize?{q}")
    print(
        "\n上のURLをブラウザで開いて承認してください。"
        "\nredirect_uri は App Dashboard の「有効なOAuthリダイレクトURI」に"
        "\n完全一致で登録されている必要があります。"
        "\n承認後、ページが開けなくても構いません。アドレスバーのURLから"
        "\ncode= の値（末尾の #_ は含めない）をコピーしてください。"
    )
    return 0


def _code_from(value: str) -> str:
    """認可コード、またはリダイレクト先URLまるごとを受け取ってコードを返す。

    ブラウザのアドレスバーからURLをそのまま貼れるようにする。
    Metaは末尾に #_ を付けて返すことがあるので取り除く。
    """
    value = value.strip().strip('"').strip("'")
    if "code=" in value:
        parsed = urllib.parse.urlparse(value)
        qs = urllib.parse.parse_qs(parsed.query)
        if "code" in qs:
            value = qs["code"][0]
        else:  # フラグメント側に付くケース
            value = urllib.parse.parse_qs(parsed.fragment).get("code", [value])[0]
    return value.split("#")[0].strip()


def cmd_exchange(a: argparse.Namespace) -> int:
    a.code = _code_from(a.code)
    if not a.code:
        print("❌ 認可コードが読み取れませんでした", file=sys.stderr)
        return 1
    print(f"認可コード: {a.code[:12]}…（{len(a.code)}文字）")
    for host in GRAPH_HOSTS:
        status, res = _req(
            f"https://{host}/oauth/access_token",
            {
                "client_id": a.app_id,
                "client_secret": a.app_secret,
                "grant_type": "authorization_code",
                "redirect_uri": a.redirect_uri,
                "code": a.code,
            },
            method="POST",
        )
        if status == 200 and isinstance(res, dict) and res.get("access_token"):
            print(f"✅ {host} で短期トークンを取得しました")
            print(f"   user_id      = {res.get('user_id')}")
            print(f"   access_token = {res['access_token']}")
            print("\n次: longlived サブコマンドで60日トークンに交換してください。")
            return 0
        print(f"❌ {host}: HTTP {status} — {_err(res)}", file=sys.stderr)
    return 1


def cmd_longlived(a: argparse.Namespace) -> int:
    for host in GRAPH_HOSTS:
        status, res = _req(
            f"https://{host}/access_token",
            {
                "grant_type": "th_exchange_token",
                "client_secret": a.app_secret,
                "access_token": a.token,
            },
        )
        if status == 200 and isinstance(res, dict) and res.get("access_token"):
            days = int(res.get("expires_in", 0)) // 86400
            print(f"✅ {host} で長期トークンを取得しました（約{days}日有効）")
            print(f"   access_token = {res['access_token']}")
            print(
                "\nこれを GitHub の Secret THREADS_ACCESS_TOKEN に入れてください。"
                "\n60日で失効し、失効すると更新できません。"
                "\nrefresh-token.yml が毎月1日・15日に自動更新します。"
            )
            return 0
        print(f"❌ {host}: HTTP {status} — {_err(res)}", file=sys.stderr)
    return 1


def cmd_check(a: argparse.Namespace) -> int:
    user_id = a.user_id or os.environ.get("THREADS_USER_ID", "")
    token = a.token or os.environ.get("THREADS_ACCESS_TOKEN", "")
    if not token:
        print("❌ トークンがありません（--token か THREADS_ACCESS_TOKEN）", file=sys.stderr)
        return 1

    working_host = None
    me: dict = {}
    for host in GRAPH_HOSTS:
        status, res = _req(
            f"https://{host}/v1.0/me",
            {"fields": "id,username", "access_token": token},
        )
        if status == 200 and isinstance(res, dict) and res.get("id"):
            working_host, me = host, res
            print(f"✅ 疎通OK: {host}")
            print(f"   id       = {res['id']}")
            print(f"   username = @{res.get('username', '?')}")
            break
        print(f"   {host}: HTTP {status} — {_err(res)}")

    if not working_host:
        print(
            "\n❌ どちらのホストでも認証できませんでした。"
            "\n   トークンの失効、権限（threads_basic）の未付与、"
            "\n   またはアプリの設定を確認してください。",
            file=sys.stderr,
        )
        return 1

    if user_id and user_id != me["id"]:
        print(
            f"\n⚠️ THREADS_USER_ID ({user_id}) と実際のID ({me['id']}) が一致しません。"
            "\n   Secret を実際のIDに直してください。"
        )
    elif not user_id:
        print(f"\n→ THREADS_USER_ID には {me['id']} を設定してください。")
        user_id = me["id"]

    # 投稿権限の確認。コンテナを作るだけで publish はしない。
    # 未公開コンテナは24時間で期限切れになり、外からは見えない。
    print("\n投稿権限を確認します（コンテナを作るだけで、公開はしません）")
    status, res = _req(
        f"https://{working_host}/v1.0/{user_id}/threads",
        {
            "media_type": "TEXT",
            "text": "[接続確認用・非公開コンテナ] このコンテナは公開されません。",
            "access_token": token,
        },
        method="POST",
    )
    if status == 200 and isinstance(res, dict) and res.get("id"):
        print(f"✅ threads_content_publish OK（コンテナ {res['id']} を作成・未公開）")
    else:
        print(f"❌ コンテナ作成に失敗: HTTP {status} — {_err(res)}", file=sys.stderr)
        print(
            "   threads_content_publish 権限が付与されていない可能性があります。",
            file=sys.stderr,
        )
        return 1

    print(f"\n接続は成立しています。使用ホスト: {working_host}")
    if working_host != GRAPH_HOSTS[0]:
        print(
            f"→ ワークフローの env に THREADS_GRAPH_HOST={working_host} を"
            "追加してください。"
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("authurl", help="認可URLを生成する")
    p.add_argument("--app-id", required=True)
    p.add_argument("--redirect-uri", required=True)
    p.set_defaults(fn=cmd_authurl)

    p = sub.add_parser("exchange", help="認可コードを短期トークンに交換する")
    p.add_argument("--app-id", required=True)
    p.add_argument("--app-secret", required=True)
    p.add_argument("--redirect-uri", required=True)
    p.add_argument(
        "--code",
        required=True,
        help="認可コード。ブラウザのアドレスバーのURLをそのまま貼ってもよい",
    )
    p.set_defaults(fn=cmd_exchange)

    p = sub.add_parser("longlived", help="短期トークンを60日トークンに交換する")
    p.add_argument("--app-secret", required=True)
    p.add_argument("--token", required=True)
    p.set_defaults(fn=cmd_longlived)

    p = sub.add_parser("check", help="接続確認（公開投稿はしない）")
    p.add_argument("--user-id", default="")
    p.add_argument("--token", default="")
    p.set_defaults(fn=cmd_check)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
