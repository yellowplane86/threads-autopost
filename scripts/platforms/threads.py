"""Threads API 実装。

公式仕様（developers.facebook.com/docs/threads）に基づく:
  - コンテナ作成: POST https://graph.threads.net/v1.0/{user-id}/threads
      media_type = TEXT | IMAGE | VIDEO | CAROUSEL
      text / image_url / reply_to_id / access_token
      -> {"id": "<container-id>"}
  - 公開:         POST https://graph.threads.net/v1.0/{user-id}/threads_publish
      creation_id / access_token
      -> {"id": "<media-id>"}
  - 本文上限 500文字
  - image_url は「公開サーバー上に存在する」必要がある（APIがcurlで取得する）
  - レート: 24時間あたり 投稿250件 / 返信1,000件
"""
from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import json

import os

from .base import Platform, PostError

# Threadsは threads.net から threads.com への移行が進んでおり、公式ドキュメントでも
# 新旧の表記が混在している。既定は新ドキュメント側の .com とし、環境変数で切替可能。
# scripts/connect.py がどちらで通るかを実測して教える。
GRAPH_HOSTS = ("graph.threads.com", "graph.threads.net")
GRAPH_HOST = os.environ.get("THREADS_GRAPH_HOST", GRAPH_HOSTS[0])
GRAPH = f"https://{GRAPH_HOST}/v1.0"
# コンテナ作成から公開までの推奨待機。画像コンテナは非同期処理されるため。
IMAGE_CONTAINER_WAIT_SEC = 30
TEXT_CONTAINER_WAIT_SEC = 3


def _post_form(url: str, params: dict[str, str], timeout: int = 60) -> dict:
    body = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        # 4xx は設定ミス（トークン・権限・URL）なのでリトライしない。
        # 429/5xx は一時的なのでリトライ可。
        retryable = e.code == 429 or e.code >= 500
        raise PostError(f"HTTP {e.code}: {detail}", retryable=retryable) from e
    except urllib.error.URLError as e:
        raise PostError(f"network error: {e.reason}", retryable=True) from e


class ThreadsPlatform(Platform):
    name = "threads"
    text_limit = 500
    needs_public_image_url = True

    def validate_secrets(self) -> None:
        for key in ("user_id", "access_token"):
            if not self.secrets.get(key):
                raise PostError(f"secret が未設定です: {key}", retryable=False)

    # -- 内部 --------------------------------------------------------------
    def _create_container(self, **extra: str) -> str:
        params = {"access_token": self.secrets["access_token"], **extra}
        res = _post_form(f"{GRAPH}/{self.secrets['user_id']}/threads", params)
        cid = res.get("id")
        if not cid:
            raise PostError(f"コンテナIDが返りませんでした: {res}", retryable=False)
        return cid

    def _publish_container(self, container_id: str) -> str:
        params = {
            "access_token": self.secrets["access_token"],
            "creation_id": container_id,
        }
        last: PostError | None = None
        # 画像コンテナの処理完了前に publish すると失敗することがあるため
        # リトライ可のエラーに限り数回試す。
        for attempt in range(4):
            try:
                res = _post_form(
                    f"{GRAPH}/{self.secrets['user_id']}/threads_publish", params
                )
                mid = res.get("id")
                if not mid:
                    raise PostError(f"投稿IDが返りませんでした: {res}", retryable=False)
                return mid
            except PostError as e:
                last = e
                if not e.retryable:
                    raise
                time.sleep(10 * (attempt + 1))
        assert last is not None
        raise last

    # -- 公開 --------------------------------------------------------------
    def publish(self, text: str, image_url: str | None) -> str:
        if len(text) > self.text_limit:
            raise PostError(
                f"本文が上限超過です: {len(text)}字 > {self.text_limit}字", retryable=False
            )
        if image_url:
            cid = self._create_container(
                media_type="IMAGE", text=text, image_url=image_url
            )
            time.sleep(IMAGE_CONTAINER_WAIT_SEC)
        else:
            cid = self._create_container(media_type="TEXT", text=text)
            time.sleep(TEXT_CONTAINER_WAIT_SEC)
        return self._publish_container(cid)

    def publish_reply(self, text: str, parent_id: str) -> str:
        if len(text) > self.text_limit:
            raise PostError(
                f"返信が上限超過です: {len(text)}字 > {self.text_limit}字", retryable=False
            )
        cid = self._create_container(
            media_type="TEXT", text=text, reply_to_id=parent_id
        )
        time.sleep(TEXT_CONTAINER_WAIT_SEC)
        return self._publish_container(cid)
