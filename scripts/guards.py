"""投稿直前ガード（fail-closed）。

承認なしの完全自動運用なので、指示書の「投稿直前チェックリスト」を
機械が実行する。1つでも外れたらその枠は投稿しない。
黙って落とすのではなく、必ず理由を返してジョブサマリーに残す。
"""
from __future__ import annotations

import datetime as dt
import urllib.error
import urllib.request
from dataclasses import dataclass, field

UA = "nissin-travel-autopost/1.0 (+github-actions)"


@dataclass
class GuardReport:
    passed: bool
    reasons: list[str] = field(default_factory=list)

    def fail(self, reason: str) -> None:
        self.passed = False
        self.reasons.append(reason)

    def note(self, reason: str) -> None:
        self.reasons.append(reason)


def _fetch(url: str, timeout: int = 30) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(600_000)
            charset = r.headers.get_content_charset() or "utf-8"
            return r.status, raw.decode(charset, errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception as e:  # noqa: BLE001 - ネットワーク全般を失敗として扱う
        return 0, f"__error__ {e}"


def _head_ok(url: str, timeout: int = 30) -> tuple[bool, str]:
    for method in ("HEAD", "GET"):
        req = urllib.request.Request(
            url, method=method, headers={"User-Agent": UA}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                ctype = r.headers.get("Content-Type", "")
                if 200 <= r.status < 300:
                    return True, ctype
                return False, f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            if method == "GET":
                return False, f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            if method == "GET":
                return False, str(e)
    return False, "unreachable"


# --- 個別ガード ----------------------------------------------------------


def check_text(post: dict, text_limit: int, rep: GuardReport) -> None:
    """本文・返信の構造検査。指示書 §11.2 のPR表記と、URL位置のルール。"""
    main = post.get("main_text", "")
    reply = post.get("reply_text") or ""
    has_link = bool(post.get("link_url"))

    if not main.strip():
        rep.fail("main_text が空です")
    if len(main) > text_limit:
        rep.fail(f"main_text が上限超過: {len(main)}字 > {text_limit}字")
    if reply and len(reply) > text_limit:
        rep.fail(f"reply_text が上限超過: {len(reply)}字 > {text_limit}字")

    if has_link:
        if not main.startswith("【PR】"):
            rep.fail("リンクありの投稿なのに main_text が【PR】で始まっていません")
        if not reply:
            rep.fail("link_url があるのに reply_text がありません")
        else:
            lines = reply.split("\n")
            if lines[0].strip() != "PR":
                rep.fail("reply_text の1行目が 'PR' ではありません")
            if "料金・空室はこちら👇" not in reply:
                rep.fail("reply_text に指定の誘導文がありません")
            urls = [w for w in reply.split() if w.startswith("http")]
            if len(urls) != 1:
                rep.fail(f"reply_text 内のURLが1本ではありません（{len(urls)}本）")
            elif urls[0] != post["link_url"]:
                rep.fail("reply_text のURLが link_url と一致しません")
    else:
        if "【PR】" in main:
            rep.fail("リンクなしの投稿に【PR】が付いています")

    if "http" in main:
        rep.fail("main_text にURLが含まれています（URLは返信に1本だけ）")


def check_expiry(post: dict, now_jst: dt.datetime, rep: GuardReport) -> None:
    """賞味期限。トマムの10/13など、過ぎたら投稿してはいけない案件を止める。"""
    exp = post.get("expires_on")
    if not exp:
        return
    try:
        d = dt.date.fromisoformat(exp)
    except ValueError:
        rep.fail(f"expires_on の書式が不正です: {exp!r}")
        return
    if now_jst.date() > d:
        rep.fail(f"賞味期限切れ（expires_on={exp}、本日={now_jst.date()}）")


def check_image(post: dict, needs_public_url: bool, rep: GuardReport) -> None:
    """画像。指示書の『画像が用意できない案件は落とす』を機械的に実行する。

    require_image=true の枠で画像URLが取得できなければ、テキストのみでの
    代替投稿はせず、枠そのものを落とす。
    """
    url = post.get("image_url")
    require = post.get("require_image", True)

    if not url:
        if require:
            rep.fail("image_url が未設定（画像が用意できない案件は落とす方針）")
        else:
            rep.note("画像なしで投稿します（require_image=false）")
        return

    if needs_public_url and not url.startswith("https://"):
        rep.fail(f"image_url がhttpsの公開URLではありません: {url[:80]}")
        return

    ok, info = _head_ok(url)
    if not ok:
        rep.fail(f"image_url に到達できません（{info}）")
    elif "image" not in info.lower() and info:
        rep.note(f"image_url のContent-Typeが画像ではない可能性: {info}")


def check_sources(post: dict, rep: GuardReport) -> None:
    """一次ソースの生存確認。

    source_checks: [{url, must_contain: [...], must_not_contain: [...]}]
    例: 新穂高ロープウェイの公式で「営業中」が消えたら運休の可能性 → 投稿しない。
    """
    for chk in post.get("source_checks", []):
        url = chk.get("url")
        if not url:
            continue
        status, body = _fetch(url)
        if status != 200:
            rep.fail(f"ソース確認に失敗: {url}（HTTP {status or 'error'}）")
            continue
        for needle in chk.get("must_contain", []):
            if needle not in body:
                rep.fail(f"ソースに必須文字列がありません: {needle!r} @ {url}")
        for needle in chk.get("must_not_contain", []):
            if needle in body:
                rep.fail(f"ソースに禁止文字列が出現: {needle!r} @ {url}")


def run_all(
    post: dict, *, text_limit: int, needs_public_image_url: bool, now_jst: dt.datetime
) -> GuardReport:
    rep = GuardReport(passed=True)
    check_text(post, text_limit, rep)
    check_expiry(post, now_jst, rep)
    check_image(post, needs_public_image_url, rep)
    check_sources(post, rep)
    return rep
