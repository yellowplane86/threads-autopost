"""プラットフォーム共通インターフェース。

Xを追加するときは、このクラスを継承した platforms/x.py を作り
PLATFORMS に登録するだけでよい。投稿データ(posts/*.json)は共通。
"""
from __future__ import annotations
from dataclasses import dataclass


class PostError(RuntimeError):
    """投稿の失敗。リトライ可否を retryable で示す。"""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass
class PostResult:
    platform: str
    main_id: str
    reply_id: str | None


class Platform:
    name: str = "base"
    #: 本文の最大文字数（プラットフォーム上限）
    text_limit: int = 0
    #: 画像を投稿する際に公開URLからの取得を要するか
    needs_public_image_url: bool = False

    def __init__(self, **secrets: str):
        self.secrets = secrets

    def validate_secrets(self) -> None:
        raise NotImplementedError

    def publish(self, text: str, image_url: str | None) -> str:
        """メイン投稿を公開し、投稿IDを返す。"""
        raise NotImplementedError

    def publish_reply(self, text: str, parent_id: str) -> str:
        """parent_id への返信を公開し、投稿IDを返す。"""
        raise NotImplementedError

    def post_with_reply(
        self, main_text: str, reply_text: str | None, image_url: str | None
    ) -> PostResult:
        main_id = self.publish(main_text, image_url)
        reply_id = None
        if reply_text:
            # メインが成功して返信が失敗した場合、メインは残る。
            # リンクなしの投稿が残るのは許容し、ログで明示する。
            reply_id = self.publish_reply(reply_text, main_id)
        return PostResult(self.name, main_id, reply_id)
