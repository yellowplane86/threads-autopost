"""posts/*.json の構造検査。commit時にCIで走らせる。

ネットワークを使う確認（ソース生存・画像到達性）は投稿直前の guards.py が担当。
ここは「形が壊れていないか」を早期に落とすためのもの。
"""
from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import guards  # noqa: E402
from platforms import PLATFORMS  # noqa: E402

REQUIRED = ("slot", "title", "main_text", "format_type")
VALID_SLOTS = ("morning", "noon", "evening", "night")
VALID_TYPES = (
    "単体スポット紹介",
    "リスト型",
    "常識の裏切り",
    "比較・選び方",
    "季節カレンダー",
)


def validate_file(path: pathlib.Path, text_limit: int) -> list[str]:
    errs: list[str] = []
    try:
        pkg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"{path.name}: JSONが壊れています — {e}"]

    if pkg.get("date") != path.stem:
        errs.append(f"{path.name}: date フィールドがファイル名と一致しません")
    try:
        day = dt.date.fromisoformat(pkg.get("date", ""))
    except ValueError:
        errs.append(f"{path.name}: date が不正です")
        day = None

    posts = pkg.get("posts", [])
    if not posts:
        errs.append(f"{path.name}: posts が空です")

    seen_slots: list[str] = []
    types_in_order: list[str] = []
    for i, p in enumerate(posts):
        tag = f"{path.name}[{i}]"
        for k in REQUIRED:
            if not p.get(k):
                errs.append(f"{tag}: {k} がありません")
        if p.get("slot") not in VALID_SLOTS:
            errs.append(f"{tag}: slot が不正です — {p.get('slot')!r}")
        elif p["slot"] in seen_slots:
            errs.append(f"{tag}: slot が重複しています — {p['slot']}")
        else:
            seen_slots.append(p["slot"])
        if p.get("format_type") and p["format_type"] not in VALID_TYPES:
            errs.append(f"{tag}: format_type が不正です — {p['format_type']!r}")
        types_in_order.append(p.get("format_type", ""))

        # テキスト構造（PR表記・URL位置・字数）はガードと同じ規則で検査
        rep = guards.GuardReport(passed=True)
        guards.check_text(p, text_limit, rep)
        if day:
            guards.check_expiry(p, dt.datetime.combine(day, dt.time(12)), rep)
        for r in rep.reasons:
            errs.append(f"{tag}: {r}")

        if p.get("require_image", True) and not p.get("image_url"):
            errs.append(
                f"{tag}: require_image なのに image_url が空です"
                "（投稿時にガードで落ちます）"
            )
        if not p.get("source_checks"):
            errs.append(f"{tag}: source_checks がありません（一次ソースの生存確認ができません）")

    # 同じ型の3連続を禁止
    for i in range(len(types_in_order) - 2):
        a, b, c = types_in_order[i : i + 3]
        if a and a == b == c:
            errs.append(f"{path.name}: 型が3連続しています — {a}")

    return errs


def main() -> int:
    limit = PLATFORMS["threads"].text_limit
    files = sorted(pathlib.Path("posts").glob("*.json"))
    if not files:
        print("posts/*.json がありません")
        return 1
    all_errs: list[str] = []
    for f in files:
        errs = validate_file(f, limit)
        status = "OK" if not errs else f"NG ({len(errs)})"
        print(f"{f.name}: {status}")
        all_errs += errs
    for e in all_errs:
        print(f"  - {e}")
    return 1 if all_errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
