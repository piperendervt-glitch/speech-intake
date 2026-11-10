"""入力機器の診断（段11）。録った音の中身から、帯域・頭の欠け・雑音・途切れを数える。

Bluetooth ヘッドセットのマイク（HFP）は 16 kHz（広帯域・mSBC）か 8 kHz（狭帯域・CVSD）のどちらかで届く。
機器が 16 kHz と申告していても、中身は 8 kHz 相当（4 kHz より上が空）のことがあるので、申告ではなく中身で見る。

判定の文言は 3 つだけ（広帯域らしい／狭帯域らしい／判断しにくい）。数字は目安で、合否ではない。
決めるのは人：閲覧画面のスペクトログラムで 4 kHz より上に「s」「sh」の成分があるかを目で確かめる。

sessions/<sid>/devcheck.json に書く。numpy だけで動く（録音と分けて、既にあるセッションにも使える）。
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import soundfile as sf

from .session import SessionStore

# 「s」「sh」「th」を含む短い英文（高い成分が出る音）。README にも同じものを置く
SENTENCES = (
    "She sells sea shells by the seashore.",
    "This fish shop sells six thick socks.",
    "Sixty-six thistles, three thin sheets.",
)

VERDICTS = {"wide": "広帯域らしい", "narrow": "狭帯域らしい", "unclear": "判断しにくい"}

FRAME_MS = 10
N_FFT = 512
HOP = 256
DB_FLOOR = -120.0


def _db(x: np.ndarray | float) -> np.ndarray | float:
    return 10.0 * np.log10(np.maximum(x, 1e-12))


def _frame_rms_db(x: np.ndarray, n: int) -> np.ndarray:
    m = len(x) // n
    if m == 0:
        return np.zeros(0)
    f = x[: m * n].reshape(m, n).astype(np.float64)
    return _db(np.mean(f * f, axis=1))


def stft_power(x: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    """|STFT|^2（Hann 窓、正規化して 0 dBFS の正弦波が約 0 dB）。形は (枠の数, n_fft/2+1)。"""
    if len(x) < n_fft:
        x = np.concatenate([x, np.zeros(n_fft - len(x), dtype=x.dtype)])
    m = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(m)[:, None]
    win = np.hanning(n_fft)
    spec = np.fft.rfft(x[idx].astype(np.float64) * win, axis=1)
    return (np.abs(spec) ** 2) * (4.0 / (np.sum(win) ** 2))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """True が続く区間 [a, b) の一覧。"""
    if not len(mask):
        return []
    d = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1)))


def analyze(x: np.ndarray, sr: int = 16000, quiet_db: float = -70.0, dropout_db: float = -85.0,
            dropout_min_ms: float = 20.0) -> dict[str, Any]:
    """音（float、-1..1）を調べる。

    head_silence_ms  先頭から quiet_db（dBFS）未満の 10 ms 枠が続いた長さ（マイクを開いた直後に接続が切り替わる間の無音）
    dropouts         頭の後ろで、dropout_db 未満（ほぼデジタルの無音）が dropout_min_ms 以上続いた所（音の途切れ・欠落）
    noise_dbfs       無音時の雑音（頭を除いた 10 ms 枠の大きさの下から 10%）
    speech_dbfs      声の大きさの目安（上から 5%）
    band             4 kHz より上の成分：hf_ratio_db（4.2〜7.8 kHz と 0.1〜4 kHz の比、声の枠だけ）、
                     cliff_db（3.2〜3.8 kHz と 4.4〜6 kHz の差。狭帯域なら 4 kHz で崖になる）、
                     upper_hz（声の平均が雑音より 6 dB 以上大きい上端）、sibilant_frames（4 kHz より上が下より強い枠の数＝「s」など）
    """
    x = np.asarray(x, dtype=np.float32).reshape(-1)
    n = int(sr * FRAME_MS / 1000)
    lv = _frame_rms_db(x, n)
    out: dict[str, Any] = {"sample_rate": sr, "duration_s": round(len(x) / sr, 3)}
    if not len(lv):
        out.update(verdict="unclear", verdict_text=VERDICTS["unclear"], reasons=["録音が短すぎる"])
        return out
    # 頭の欠け
    loud = np.flatnonzero(lv >= quiet_db)
    head = int(loud[0]) if len(loud) else len(lv)
    out["head_silence_ms"] = head * FRAME_MS
    body = lv[head:]
    # 途切れ（頭の後ろ、ほぼデジタルの無音が続いた所。録音の終わりに掛かるものも数える）
    drops = []
    for a, b in _runs(body < dropout_db):
        if (b - a) * FRAME_MS >= dropout_min_ms:
            drops.append({"t": round(float(head + a) * FRAME_MS / 1000, 2), "ms": int((b - a) * FRAME_MS)})
    out["dropouts"] = drops[:50]
    out["dropout_count"] = len(drops)
    out["dropout_total_ms"] = int(sum(d["ms"] for d in drops))
    if len(body) < 10:
        out.update(verdict="unclear", verdict_text=VERDICTS["unclear"], reasons=["頭の無音の後ろに音がほとんど無い"])
        return out
    live = body[body >= dropout_db]
    live = live if len(live) else body
    noise = float(np.percentile(live, 10))
    speech = float(np.percentile(live, 95))
    out["noise_dbfs"] = round(noise, 1)
    out["speech_dbfs"] = round(speech, 1)
    out["snr_db"] = round(speech - noise, 1)
    out["clipped_ratio"] = round(float(np.mean(np.abs(x) >= 0.999)), 5)

    # 帯域：声の枠（雑音より 15 dB 以上大きい）の平均スペクトル
    seg = x[head * n:]
    P = stft_power(seg)
    freqs = np.fft.rfftfreq(N_FFT, 1 / sr)
    m = P.shape[0]
    idx = np.arange(N_FFT)[None, :] + HOP * np.arange(m)[:, None]
    segp = np.concatenate([seg, np.zeros(max(0, N_FFT - len(seg)), dtype=seg.dtype)])
    fl = _db(np.mean(segp[idx].astype(np.float64) ** 2, axis=1))  # 枠の大きさ（10 ms 枠と同じ物差し）
    act = fl >= noise + 15.0
    quiet = fl <= np.percentile(fl, 20)
    active_s = float(np.sum(act) * HOP / sr)
    band: dict[str, Any] = {"active_s": round(active_s, 2), "declared_nyquist_hz": sr / 2}
    reasons: list[str] = []
    if active_s < 0.5:
        reasons.append(f"声らしい所が {active_s:.2f} 秒しかない（雑音より 15 dB 以上大きい所）。もう少し大きな声で長めに読む")
        out["band"] = band
        out.update(verdict="unclear", verdict_text=VERDICTS["unclear"], reasons=reasons)
        return out

    def bandsum(Pm, lo, hi):
        return float(np.sum(Pm[(freqs >= lo) & (freqs < hi)]))

    Pa = P[act].mean(axis=0)
    Pn = P[quiet].mean(axis=0) if np.any(quiet) else np.full_like(Pa, 1e-12)
    hf_ratio = _db(bandsum(Pa, 4200, 7800) / max(bandsum(Pa, 100, 4000), 1e-20))
    def sm(v):  # 5 本の移動平均（端は端の値で埋める）
        d = np.pad(_db(v), 2, mode="edge")
        return np.convolve(d, np.ones(5) / 5, mode="valid")

    da, dn = sm(Pa), sm(Pn)
    m1 = (freqs >= 3200) & (freqs < 3800)
    m2 = (freqs >= 4400) & (freqs < 6000)
    cliff = float(np.mean(da[m1]) - np.mean(da[m2]))
    # 雑音より 6 dB 以上、かつ声の山から 50 dB 以内（リサンプルの漏れのような極小の値を数えない）
    above = np.flatnonzero((da > dn + 6.0) & (da > np.max(da) - 50.0) & (freqs >= 300) & (freqs <= sr / 2 - 200))
    upper = float(freqs[above[-1]]) if len(above) else 0.0
    hi_e = P[act][:, freqs >= 4000].sum(axis=1)
    lo_e = P[act][:, (freqs >= 100) & (freqs < 4000)].sum(axis=1)
    sib = int(np.sum(hi_e > lo_e))
    band.update(hf_ratio_db=round(float(hf_ratio), 1), cliff_db=round(cliff, 1), upper_hz=round(upper),
                sibilant_frames=sib)
    out["band"] = band
    out["spectrum_db"] = {"hz": [round(float(f)) for f in freqs[::4]], "speech": [round(float(v), 1) for v in da[::4]],
                          "noise": [round(float(v), 1) for v in dn[::4]]}

    # 判定（目安）。狭帯域：4 kHz の崖がはっきりしていて、上に声が無い。広帯域：上端が 5.5 kHz 以上で「s」の枠がある
    if cliff >= 25 and sib == 0 and (upper <= 4400 or hf_ratio <= -45):
        v = "narrow"
        reasons.append(f"4 kHz を挟んで {cliff:.0f} dB 落ち、4 kHz より上の比は {hf_ratio:.0f} dB、s などの枠が無い"
                       f"（声が雑音より 6 dB 以上大きいのは {upper:.0f} Hz まで）")
    elif upper >= 5500 and sib >= 3:
        v = "wide"
        reasons.append(f"声が雑音より 6 dB 以上大きいのは {upper:.0f} Hz まで、4 kHz より上が強い枠（s など）が {sib} 個")
    else:
        v = "unclear"
        reasons.append(f"上端 {upper:.0f} Hz・4 kHz の段差 {cliff:.0f} dB・s などの枠 {sib} 個。どちらとも言いにくい")
        if sib < 3 and upper >= 5500:
            reasons.append("s・sh を含む英文をはっきり読んで、もう一度")
    if out["head_silence_ms"] >= 300:
        reasons.append(f"頭に {out['head_silence_ms']} ms の無音（マイクを開いた直後の切り替えの間らしい）")
    if drops:
        reasons.append(f"途中で音が {len(drops)} 回途切れた（計 {out['dropout_total_ms']} ms）")
    out.update(verdict=v, verdict_text=VERDICTS[v], reasons=reasons)
    return out


def analyze_session(store: SessionStore, sid: str, write: bool = True) -> dict[str, Any]:
    x, sr = sf.read(str(store.audio_path(sid)), dtype="float32", always_2d=False)
    if x.ndim > 1:
        x = x[:, 0]
    meta = store.read_meta(sid)
    rep = {"session": sid, **analyze(x, sr)}
    rep["device"] = {k: meta.get(k) for k in ("device", "device_name", "hostapi", "device_default_rate", "device_rate",
                                                "resampled", "overflows", "lost") if meta.get(k) is not None}
    dr = meta.get("device_rate") or meta.get("device_default_rate")
    if dr and float(dr) <= 8000:
        rep.setdefault("reasons", []).insert(0, f"機器が {float(dr):.0f} Hz と申告している（4 kHz より上はそもそも届かない）")
    rep["note"] = "数字は目安。閲覧画面のスペクトログラムで 4 kHz より上に s・sh の成分があるかを見て、人が決める"
    if write:
        (store.dir(sid) / "devcheck.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    return rep


def format_report(rep: dict[str, Any]) -> str:
    d = rep.get("device") or {}
    b = rep.get("band") or {}
    lines = [f"== devcheck {rep['session']} =="]
    if d:
        lines.append(f"機器        {d.get('device_name', d.get('device', '?'))}（{d.get('hostapi', '?')}）"
                     f" 申告 {d.get('device_default_rate', '?')} Hz / 開いた {d.get('device_rate', '?')} Hz"
                     + ("（16 kHz に直して保存）" if d.get("resampled") else ""))
    lines.append(f"判定（目安） {rep.get('verdict_text', '?')}")
    for r in rep.get("reasons", []):
        lines.append(f"  - {r}")
    if b.get("upper_hz") is not None:
        lines.append(f"帯域        上端 {b['upper_hz']} Hz / 4 kHz 上の比 {b['hf_ratio_db']} dB / 4 kHz の段差 {b['cliff_db']} dB"
                     f" / s などの枠 {b['sibilant_frames']} / 声の長さ {b['active_s']} s")
    if "head_silence_ms" in rep:
        lines.append(f"頭の欠け    {rep['head_silence_ms']} ms")
    if "noise_dbfs" in rep:
        lines.append(f"雑音        {rep['noise_dbfs']} dBFS（声 {rep['speech_dbfs']} dBFS、差 {rep['snr_db']} dB）")
    if "dropout_count" in rep:
        lines.append(f"途切れ      {rep['dropout_count']} 回（計 {rep['dropout_total_ms']} ms）"
                     + ("".join(f"  {x['t']}s:{x['ms']}ms" for x in rep["dropouts"][:8])))
    lines.append(rep.get("note", ""))
    return "\n".join(lines)
