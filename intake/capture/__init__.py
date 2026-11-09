"""マイク取り込み。16 kHz mono で受け、セッション単位で WAV に保存しながら AudioChunk を流す。

取り込み以外の段は生のマイクに依存しない。ここだけが sounddevice を import する。
"""

from .mic import MicCapture, list_input_devices

__all__ = ["MicCapture", "list_input_devices"]
