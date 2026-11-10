"""WAV を JA1 行の UDP で実時間で送る（段11。AirPods が無くても `record --input ja1` を試すための送り手）。

  python tools/send_ja1.py samples/conv2_ja_kokoro.wav              # 127.0.0.1:9750 へ 20 ms ごと
  python tools/send_ja1.py in.wav --port 9751 --drop 0.02           # 2% のパケットを捨てて送る（抜けの埋め方を見る）

形は subvocal-robot の windows/jawrec/hfp_mic.py と同じ（JA1,<source>,<seq>,<t>,16000,<base64 int16>、1 行 = 320 サンプル）。
"""

import argparse
import random
import socket
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intake.capture.ja1 import encode_ja1  # noqa: E402

CHUNK = 320


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=9750)
    ap.add_argument("--source", default="send-ja1")
    ap.add_argument("--drop", type=float, default=0.0, help="捨てるパケットの割合（0〜1）")
    ap.add_argument("--lead", type=float, default=0.0, help="先頭に足す無音の秒")
    a = ap.parse_args()
    x, sr = sf.read(a.wav, dtype="int16", always_2d=True)
    if sr != 16000:
        raise SystemExit(f"16 kHz の WAV だけ: {sr} Hz（python -m intake import で直してから）")
    pcm = np.concatenate([np.zeros(int(a.lead * sr), dtype=np.int16), x[:, 0]])
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    t0 = time.monotonic()
    sent = dropped = 0
    for seq, i in enumerate(range(0, len(pcm) - CHUNK + 1, CHUNK)):
        t = t0 + i / sr
        wait = t + CHUNK / sr - time.monotonic()  # マイクと同じく、20 ms 分が揃った時刻に送る
        if wait > 0:
            time.sleep(wait)
        if a.drop and random.random() < a.drop:
            dropped += 1
            continue
        s.sendto(encode_ja1(a.source, seq, t, sr, pcm[i : i + CHUNK]), (a.host, a.port))
        sent += 1
    print(f"送った {sent} 行、捨てた {dropped} 行（{len(pcm) / sr:.1f} 秒）", file=sys.stderr)


if __name__ == "__main__":
    main()
