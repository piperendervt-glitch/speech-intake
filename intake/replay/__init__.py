"""保存した WAV を流し直す入口。実時間速度（realtime）と最速（fast）の両方。"""

from .wav import WavReplay, import_audio

__all__ = ["WavReplay", "import_audio"]
