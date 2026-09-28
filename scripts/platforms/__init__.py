from .base import Platform, PostError, PostResult
from .threads import ThreadsPlatform

# Xを追加するときは platforms/x.py に XPlatform を作り、ここに1行足す。
PLATFORMS: dict[str, type[Platform]] = {
    ThreadsPlatform.name: ThreadsPlatform,
}

__all__ = ["Platform", "PostError", "PostResult", "PLATFORMS"]
