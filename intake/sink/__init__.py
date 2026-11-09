"""発話レコードの出力先。JSONL 追記（段1）と WebSocket 配信（段5）。"""

from .base import Sink, MultiSink
from .jsonl import JsonlSink

__all__ = ["Sink", "MultiSink", "JsonlSink"]
