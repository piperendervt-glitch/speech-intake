"""少しずつ届く音声のリサンプル（段11）。機器のレートが 16 kHz でないときに、取り込みで 16 kHz に直す。

窓つき sinc の帯域制限補間（Kaiser 窓）。下げるときは出力のナイキストの少し下で切る（折り返しを防ぐ）。
状態を持つので、ブロックの切れ目で音が途切れない。遅れは片側の窓の長さ分（48 kHz → 16 kHz で約 1 ms）。
scipy / soxr を入れずに numpy だけで動かす（マイクの取り込みは軽い依存のまま）。
"""

from __future__ import annotations

import numpy as np


class StreamResampler:
    def __init__(self, in_sr: int, out_sr: int, zeros: int = 16, beta: float = 8.0, rolloff: float = 0.95):
        self.in_sr, self.out_sr = int(in_sr), int(out_sr)
        self.same = self.in_sr == self.out_sr
        self.cutoff = min(1.0, self.out_sr / self.in_sr) * (rolloff if self.in_sr != self.out_sr else 1.0)
        self.half = int(np.ceil(zeros / self.cutoff))  # 片側の窓（入力のサンプル数）
        self.beta = beta
        self._buf = np.zeros(self.half, dtype=np.float64)  # 先頭の前は無音とみなす
        self._off = -self.half  # _buf[0] の入力サンプル番号
        self._n = 0  # 次に出す出力サンプル番号
        self._j = np.arange(-self.half + 1, self.half + 1)
        self._i0b = float(np.i0(beta))

    def _kernel(self, t: np.ndarray) -> np.ndarray:
        x = np.clip(t / self.half, -1.0, 1.0)
        w = np.i0(self.beta * np.sqrt(1.0 - x * x)) / self._i0b
        return self.cutoff * np.sinc(self.cutoff * t) * w

    def process(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64).reshape(-1)
        if self.same:
            return x.astype(np.float32)
        self._buf = np.concatenate([self._buf, x])
        end = self._off + len(self._buf)  # 届いた入力の終わり（この番号は無い）
        m = end - 1 - self.half  # 出力の中心がここまでなら、窓の右端まで届いている
        if m < 0:
            return np.zeros(0, dtype=np.float32)
        n_max = ((m + 1) * self.out_sr - 1) // self.in_sr
        if n_max < self._n:
            return np.zeros(0, dtype=np.float32)
        ns = np.arange(self._n, n_max + 1, dtype=np.int64)
        num = ns * self.in_sr
        base = num // self.out_sr
        frac = (num % self.out_sr) / self.out_sr
        idx = base[:, None] + self._j[None, :] - self._off
        t = self._j[None, :] - frac[:, None]
        y = np.sum(self._buf[idx] * self._kernel(t), axis=1)
        self._n = n_max + 1
        nb = (self._n * self.in_sr) // self.out_sr  # 次の出力の中心
        keep_from = nb - self.half + 1 - self._off
        if keep_from > 0:
            self._buf = self._buf[keep_from:]
            self._off += keep_from
        return y.astype(np.float32)

    def flush(self) -> np.ndarray:
        """残りを出し切る（終わりの後ろは無音とみなす）。入力の長さに見合う数だけ返す。"""
        if self.same:
            return np.zeros(0, dtype=np.float32)
        end = self._off + len(self._buf)
        total = (end * self.out_sr + self.in_sr - 1) // self.in_sr  # 入力全体に見合う出力の数
        y = self.process(np.zeros(self.half + 1))
        keep = max(0, total - (self._n - len(y)))
        return y[:keep]


def resample(x: np.ndarray, in_sr: int, out_sr: int) -> np.ndarray:
    r = StreamResampler(in_sr, out_sr)
    return np.concatenate([r.process(x), r.flush()])
