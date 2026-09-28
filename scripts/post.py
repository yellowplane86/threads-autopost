"""1枠を投稿する。GitHub Actions の cron から呼ばれる。

使い方:
    python scripts/post.py --slot auto           # 現在時刻から対象枠を判定
    python scripts/post.py --slot evening        # 枠を明示
    python scripts/post.py --slot auto --dry-run # ガードだけ走らせて投稿しない

環境変数:
    THREADS_USER_ID, THREADS_ACCESS_TOKEN   （必須）
    POSTS_DIR   既定 posts
    STATE_FILE  既定 state/posted.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import guards  # noqa: E402
from platforms import PLATFORMS, PostError  # noqa: E402

JST = dt.timezone(dt.timedelta(hours=9))

# 枠の定義。JSTの投稿時刻。
SLOTS = {
    "morning": (7, 30),
    "noon": (12, 15),
    "evening": (18, 30),
    "night": (21, 30),
}
# cronは早めに起動し、この上限まで目標時刻を待つ。
# GitHub Actionsのcronは分単位で遅延するため、精度はここで担保する。
MAX_WAIT_SEC = 20 * 60


def log(msg: str) -> None:
    print(msg, flush=True)


def summary(msg: str) -> None:
    """GitHub Actions のジョブサマリーに残す（後から何が起きたか追える）。"""
    log(msg)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")


def pick_slot(now: dt.datetime) -> str:
    """現在時刻にいちばん近い（過去30分〜未来25分の）枠を選ぶ。"""
    best, best_delta = None, None
    for name, (h, m) in SLOTS.items():
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        delta = (target - now).total_seconds()
        if -30 * 60 <= delta <= 25 * 60:
            if best_delta is None or abs(delta) < abs(best_delta):
                best, best_delta = name, delta
    if best is None:
        raise SystemExit(
            f"現在時刻 {now:%H:%M} JST に対応する枠がありません。--slot で指定してください。"
        )
    return best


def wait_until(slot: str, now: dt.datetime) -> None:
    h, m = SLOTS[slot]
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    delay = (target - now).total_seconds()
    if delay <= 0:
        log(f"目標時刻 {target:%H:%M} JST を {-delay:.0f}秒 過ぎています。即時投稿します。")
        return
    delay = min(delay, MAX_WAIT_SEC)
    log(f"目標時刻 {target:%H:%M} JST まで {delay:.0f}秒 待機します。")
    time.sleep(delay)


def load_state(path: pathlib.Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_state(path: pathlib.Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", default="auto", choices=["auto", *SLOTS])
    ap.add_argument("--platform", default="threads", choices=list(PLATFORMS))
    ap.add_argument("--date", default=None, help="YYYY-MM-DD（既定: 今日のJST日付）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    args = ap.parse_args()

    now = dt.datetime.now(JST)
    slot = pick_slot(now) if args.slot == "auto" else args.slot
    day = args.date or now.strftime("%Y-%m-%d")

    posts_dir = pathlib.Path(os.environ.get("POSTS_DIR", "posts"))
    state_file = pathlib.Path(os.environ.get("STATE_FILE", "state/posted.json"))
    pkg_path = posts_dir / f"{day}.json"

    summary(f"## {day} / {slot} / {args.platform}")

    if not pkg_path.exists():
        summary(f"❌ 投稿データがありません: `{pkg_path}` — 生成ルーティンが失敗した可能性があります。")
        return 1

    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    posts = {p["slot"]: p for p in pkg.get("posts", [])}
    if slot not in posts:
        summary(f"⏭ この日のデータに `{slot}` 枠はありません。スキップします。")
        return 0
    post = posts[slot]

    # 二重投稿防止
    state = load_state(state_file)
    key = f"{day}/{slot}/{args.platform}"
    if key in state:
        summary(f"⏭ 既に投稿済みです（{state[key].get('main_id')}）。スキップします。")
        return 0

    cls = PLATFORMS[args.platform]
    platform = cls(
        user_id=os.environ.get("THREADS_USER_ID", ""),
        access_token=os.environ.get("THREADS_ACCESS_TOKEN", ""),
    )

    # --- ガード（承認の代わり） ---
    rep = guards.run_all(
        post,
        text_limit=cls.text_limit,
        needs_public_image_url=cls.needs_public_image_url,
        now_jst=now,
    )
    for r in rep.reasons:
        summary(f"- {r}")
    if not rep.passed:
        summary(f"🛑 **ガードで停止しました。`{slot}` 枠は投稿していません。**")
        # ガード停止は「正常に止めた」なのでジョブは失敗させない。
        # ただし気づけるよう、直後に通知ステップが読む印を残す。
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
                f.write("blocked=true\n")
                f.write(f"blocked_reason={rep.reasons[0] if rep.reasons else 'unknown'}\n")
        return 0
    summary(f"✅ ガード通過（{len(rep.reasons)}件の注記）")

    if args.dry_run:
        summary("🧪 dry-run のため投稿しません。")
        log("--- main ---\n" + post["main_text"])
        if post.get("reply_text"):
            log("--- reply ---\n" + post["reply_text"])
        return 0

    try:
        platform.validate_secrets()
    except PostError as e:
        summary(f"❌ {e}")
        return 1

    if not args.no_wait:
        wait_until(slot, dt.datetime.now(JST))

    try:
        result = platform.post_with_reply(
            post["main_text"], post.get("reply_text"), post.get("image_url")
        )
    except PostError as e:
        summary(f"❌ 投稿に失敗しました: {e}")
        return 1

    state[key] = {
        "main_id": result.main_id,
        "reply_id": result.reply_id,
        "posted_at": dt.datetime.now(JST).isoformat(),
        "title": post.get("title"),
    }
    save_state(state_file, state)
    summary(
        f"📤 投稿しました — main `{result.main_id}` / reply `{result.reply_id}`"
        f"（{post.get('title', '')}）"
    )
    if result.reply_id is None and post.get("link_url"):
        summary("⚠️ 返信が作成されていません。メイン投稿にリンクが付いていない状態です。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
