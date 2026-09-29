"""link_url から返信本文を組み立てて posts/*.json に書き戻す。

返信の形は固定（2026-09-29 SAITOさん指定）。PR表記は返信にだけ入れる:
    料金・空室はこちら👇pr
    <URL>

使い方:
    python scripts/build_reply.py posts/2026-09-29.json
    python scripts/build_reply.py posts/2026-09-29.json --set noon=https://...
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from guards import REPLY_TEMPLATE as TEMPLATE


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=pathlib.Path)
    ap.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="SLOT=URL",
        help="枠にURLを設定する（例: --set noon=https://...）",
    )
    args = ap.parse_args()

    pkg = json.loads(args.path.read_text(encoding="utf-8"))
    by_slot = {p["slot"]: p for p in pkg["posts"]}

    for pair in args.set:
        slot, _, url = pair.partition("=")
        if slot not in by_slot:
            print(f"枠が見つかりません: {slot}", file=sys.stderr)
            return 1
        if not url.startswith("https://"):
            print(f"httpsのURLを指定してください: {url!r}", file=sys.stderr)
            return 1
        by_slot[slot]["link_url"] = url

    missing = []
    for slot, p in by_slot.items():
        url = p.get("link_url", "")
        if not url:
            missing.append(slot)
            p["reply_text"] = ""
            continue
        p["reply_text"] = TEMPLATE.format(url=url)
        print(f"{slot}: 返信を生成しました（{len(p['reply_text'])}字）")

    args.path.write_text(
        json.dumps(pkg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if missing:
        print(
            "\nlink_url が未設定の枠: "
            + ", ".join(missing)
            + "\nこれらは投稿直前ガードで停止します（リンクのない枠は流しません）。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
