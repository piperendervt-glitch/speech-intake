"""JA1 行（UDP）からの取り込み（段11）。subvocal-robot の airpods.bat が送る AirPods のマイクなど。

Windows のマイクとして見えない入力（2 本目の Bluetooth アダプタで受けた AirPods の HFP mSBC 16 kHz）を、
マイクと同じように sessions/<sid>/audio.wav に書きつつ AudioChunk として流す。

1 つの UDP データグラムは UTF-8 の行を 1 つ以上持つ（subvocal-robot の windows/jawrec/protocol.py と同じ形）：

    JA1,<source>,<seq>,<t>,<rate>,<base64 の little-endian int16 mono PCM>

  source  送り手の名前（例 airpods-win）
  seq     送り手ごとの通し番号（抜けた数を数える）
  t       先頭サンプルの、送り手の時計（秒。原点は任意。airpods.bat は time.monotonic）
  rate    サンプリング周波数（16000）

同じポートに来る JR1 行（頭の動き）は読み飛ばす。

時刻はマイクと同じく「書いたサンプル数 / sample_rate」で決める。パケットが抜けた・送り手が止まっていた分は、
送り手の時計 t の飛びを見て無音で埋める（音声の時計が実時間からずれていかないように）。埋めた量は session.json に残す。
待ち受けは localhost のみ。
"""

from __future__ import annotations

import base64
import binascii
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator

import numpy as np
import soundfile as sf

from ..audio import AudioChunk
from ..config import LOCAL_HOSTS
from ..session import SessionStore

MAGIC = "JA1"


@dataclass
class Ja1Packet:
    source: str
    seq: int
    t: float
    rate: int
    pcm: np.ndarray  # int16 mono


def decode_ja1(datagram: bytes) -> list[Ja1Packet]:
    """データグラムの中の JA1 行を取り出す。形の合わない行（JR1 など）は読み飛ばす。"""
    out: list[Ja1Packet] = []
    for line in datagram.decode("utf-8", errors="replace").splitlines():
        parts = line.strip().split(",")
        if len(parts) != 6 or parts[0] != MAGIC:
            continue
        try:
            seq, t, rate = int(parts[2]), float(parts[3]), int(parts[4])
            raw = base64.b64decode(parts[5], validate=True)
        except (ValueError, binascii.Error):
            continue
        if not np.isfinite(t) or rate <= 0 or len(raw) % 2:
            continue
        out.append(Ja1Packet(parts[1], seq, t, rate, np.frombuffer(raw, dtype="<i2")))
    return out


def encode_ja1(source: str, seq: int, t: float, rate: int, pcm: np.ndarray) -> bytes:
    """試験用の送り手（tools/send_ja1.py）とテストが使う。"""
    data = base64.b64encode(np.asarray(pcm, dtype="<i2").tobytes()).decode("ascii")
    return f"{MAGIC},{source},{seq},{t:.6f},{rate},{data}\n".encode("ascii")


class Ja1UdpCapture:
    """JA1 行を UDP で受け、sessions/<sid>/audio.wav に書きつつチャンクを返す（MicCapture と同じ使い方）。

    source      受ける送り手の名前。None なら最初に届いた送り手に決め、他の送り手の行は数えるだけで捨てる
    gap_fill_ms 送り手の時計がこれより大きく飛んだら、その分を無音で埋める（1 行 = 20 ms なので、1 行の抜けから埋まる）
    max_fill_s  1 回に埋める無音の上限（送り手が再起動して時計が飛んだときに埋めすぎない）
    """

    def __init__(
        self,
        store: SessionStore,
        session: str,
        sample_rate: int = 16000,
        host: str = "127.0.0.1",
        port: int = 9750,
        source: str | None = None,
        gap_fill_ms: float = 10.0,
        max_fill_s: float = 30.0,
        flush_every_s: float = 1.0,
    ):
        if host not in LOCAL_HOSTS:
            raise ValueError(f"JA1 の待ち受けは localhost のみ: {host!r}")
        self.store = store
        self.session = session
        self.sample_rate = sample_rate
        self.host, self.port = host, port
        self.source = source
        self.gap_fill = gap_fill_ms / 1000
        self.max_fill = max_fill_s
        self._flush_every = flush_every_s
        self._q: queue.Queue[tuple[float, Ja1Packet]] = queue.Queue()
        self._stop = threading.Event()
        self._prepared = False
        self._sock: socket.socket | None = None
        self.max_samples: int | None = None  # これだけ録ったら止める（record --seconds）
        self.samples_written = 0
        self.t0_unix: float | None = None
        self.stats = {"packets": 0, "lost_packets": 0, "filled_s": 0.0, "gaps": 0, "ignored_other_source": 0, "ignored_rate": 0}

    def stop(self) -> None:
        self._stop.set()

    def prepare(self) -> None:
        """ポートを開け、セッションを作る。run の出力（runs/<run>/）を置く前に呼ぶ。"""
        if self._prepared:
            return
        s = socket.socket(socket.AF_INET6 if ":" in self.host else socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind(("127.0.0.1" if self.host == "localhost" else self.host, self.port))
        except OSError as e:
            s.close()
            raise RuntimeError(
                f"udp://{self.host}:{self.port} を開けない（{e}）。同じポートを jawrec / jawchat が使っていないか。"
                "別のポートにするなら airpods.bat に --udp 127.0.0.1:9751、こちらに --set capture.udp.port=9751"
            ) from e
        s.settimeout(0.2)
        self._sock = s
        self.port = s.getsockname()[1]  # port=0（テスト）のとき、実際に開いた番号
        self.store.create(
            self.session,
            {
                "source": "ja1_udp",
                "device": f"udp://{self.host}:{self.port}",
                "sample_rate": self.sample_rate,
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "status": "recording",
            },
        )
        self._prepared = True
        threading.Thread(target=self._recv, name="intake-ja1", daemon=True).start()

    def _recv(self) -> None:
        sock = self._sock
        try:
            while not self._stop.is_set():
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                now = time.time()
                for pkt in decode_ja1(data):
                    self._q.put((now, pkt))
        finally:
            sock.close()

    def chunks(self) -> Iterator[AudioChunk]:
        self.prepare()
        sr = self.sample_rate
        path = self.store.audio_path(self.session)
        last_flush = time.monotonic()
        next_t: float | None = None  # 送り手の時計で、次に来るはずの先頭サンプルの時刻
        last_seq: int | None = None
        waiting_since = time.monotonic()
        told = False
        with sf.SoundFile(path, "w", samplerate=sr, channels=1, subtype="PCM_16") as wav:

            def write(x: np.ndarray) -> AudioChunk:
                t0 = self.samples_written / sr
                wav.write(x)
                self.samples_written += len(x)
                return AudioChunk(t0=t0, samples=x, sample_rate=sr)

            try:
                while not self._stop.is_set():
                    try:
                        arrived, pkt = self._q.get(timeout=0.1)
                    except queue.Empty:
                        if self.samples_written == 0 and not told and time.monotonic() - waiting_since > 3:
                            print(f"[intake] JA1 がまだ届かない（udp://{self.host}:{self.port}）。airpods.bat は動いているか", file=sys.stderr)
                            told = True
                        continue
                    if self.source is None:
                        self.source = pkt.source
                        self.store.update_meta(self.session, ja1_source=pkt.source)
                    if pkt.source != self.source:
                        self.stats["ignored_other_source"] += 1
                        continue
                    if pkt.rate != sr or len(pkt.pcm) == 0:
                        self.stats["ignored_rate"] += 1
                        continue
                    self.stats["packets"] += 1
                    if last_seq is not None and pkt.seq > last_seq + 1:
                        self.stats["lost_packets"] += pkt.seq - last_seq - 1
                    last_seq = pkt.seq
                    if next_t is None:
                        # 先頭サンプルの壁時計（概算）と送り手の時計。外部の時刻をセッション秒に直す手がかり
                        self.t0_unix = arrived - len(pkt.pcm) / sr
                        self.store.update_meta(self.session, t0_unix=round(self.t0_unix, 4), ja1_t0=round(pkt.t, 6))
                    else:
                        gap = pkt.t - next_t
                        if gap > self.gap_fill:  # 抜けた・止まっていた分を無音で埋める
                            n = int(round(min(gap, self.max_fill) * sr))
                            self.stats["gaps"] += 1
                            self.stats["filled_s"] = round(self.stats["filled_s"] + n / sr, 3)
                            yield write(np.zeros(n, dtype=np.float32))
                    next_t = pkt.t + len(pkt.pcm) / sr
                    yield write(pkt.pcm.astype(np.float32) / 32768.0)
                    if time.monotonic() - last_flush > self._flush_every:
                        wav.flush()  # 録音中でも閲覧画面から読めるように
                        last_flush = time.monotonic()
                    if self.max_samples is not None and self.samples_written >= self.max_samples:
                        self.stop()
            finally:
                self._stop.set()
                self.store.update_meta(self.session, status="done", duration=round(self.samples_written / sr, 3), ja1=self.stats)
