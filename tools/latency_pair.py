"""2 つの入力機器で同時に録り、同じ音（手を叩く）のずれから入力の遅れの差を測る（段11）。

  python tools/latency_pair.py --a "マイク配列@MME" --b "AirPods@MME" --seconds 15
      A（基準。PC のマイク）と B（Bluetooth など）で同時に録る。録っている間に、数秒おきに 5 回ほど手を叩く
  python tools/latency_pair.py --analyze SESSION_A SESSION_B
      録らずに、既にある 2 つのセッションで測り直す

やること：
  1. 2 つのセッション（sessions/<日時>_pair-a / _pair-b）を同時に録る（16 kHz に直して保存。録り方は record と同じ）
  2. 録音開始の壁時計（session.json の t0_unix）で B を A の時刻に合わせる
  3. 手を叩いた音の立ち上がり（包絡）の相互相関で、残りのずれ（B が A より何 ms 遅いか）を求める。叩いた 1 回ずつのずれも出す
  4. 結果を両方の session.json の pair と、A の latency_pair.json に書く。閲覧画面で A を開くと B が並び、ずれが目で見える

t0_unix は「最初のブロックが届いた時刻 - そのブロックの長さ」。speech_start の t_unix も届いた時刻で決まるので、
ここで測る値はそのまま t_unix の補正（capture.latency_ms）に使える。A 自身の遅れ（PC のマイクの数十 ms）は含まない（差だけ）。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from intake.config import load_config  # noqa: E402
from intake.session import SessionStore, new_session_id  # noqa: E402


def envelope_ms(x: np.ndarray, sr: int) -> np.ndarray:
    """1 ms ごとの立ち上がりの強さ（差分の絶対値の最大）。低い音（声・空調）より、叩いた音の鋭い立ち上がりを拾う。"""
    d = np.abs(np.diff(x.astype(np.float64), prepend=0.0))
    n = sr // 1000
    m = len(d) // n
    return d[: m * n].reshape(m, n).max(axis=1) if m else np.zeros(0)


def _norm(e: np.ndarray) -> np.ndarray:
    if not len(e):
        return e
    top = np.percentile(e, 99.7)
    e = np.minimum(e, top) / (top + 1e-12)
    return e - e.mean()


def _shift(e: np.ndarray, k: int, n: int) -> np.ndarray:
    """out[i] = e[i + k]（範囲外は 0）。長さ n。"""
    out = np.zeros(n)
    a, b = max(0, -k), min(n, len(e) - k)
    if b > a:
        out[a:b] = e[a + k: b + k]
    return out


def clap_times(e: np.ndarray, min_gap_ms: int = 300, rel: float = 0.5) -> list[int]:
    """包絡の山（叩いた所、ms）。いちばん大きな山の rel 倍以上で、min_gap_ms 以上離れたもの。"""
    if not len(e):
        return []
    thr = rel * e.max()
    idx = np.flatnonzero(e >= thr)
    out: list[int] = []
    for i in idx:
        if out and i - out[-1] < min_gap_ms:
            if e[i] > e[out[-1]]:
                out[-1] = int(i)
            continue
        out.append(int(i))
    return out


def measure(xa: np.ndarray, xb: np.ndarray, sr: int, wall_offset_s: float, max_ms: int = 1000) -> dict:
    """B の時刻 = A の時刻 + wall_offset_s で合わせた後、B が A より何 ms 遅いか（lag_ms）。"""
    ea, eb = envelope_ms(xa, sr), envelope_ms(xb, sr)
    w = int(round(wall_offset_s * 1000))
    n = len(ea)
    a = _norm(ea)
    bf = _norm(_shift(eb, w - max_ms, n + 2 * max_ms))  # bf[j] = B の、A の時刻 j - max_ms に当たる所
    sc = np.correlate(bf, a, mode="valid")  # sc[k] = Σ a[i]・bf[i + k]、ずれ = k - max_ms
    k = int(np.argmax(sc))
    best, best_l = float(sc[k]), k - max_ms
    second = np.max(np.where(np.abs(np.arange(-max_ms, max_ms + 1) - best_l) > 30, sc, -np.inf))
    claps = []
    for t in clap_times(ea):
        c = t + w + best_l  # B の中で探す位置
        lo, hi = max(0, c - 60), min(len(eb), c + 61)
        if hi <= lo:
            continue
        j = lo + int(np.argmax(eb[lo:hi]))
        claps.append({"t_a": round(t / 1000, 3), "lag_ms": int(j - w - t)})
    lags = [c["lag_ms"] for c in claps]
    return {"wall_offset_s": round(wall_offset_s, 4), "lag_ms": int(best_l),
            "peak_ratio": round(best / second, 2) if second > 0 else None,
            "claps": claps, "lag_ms_median": int(np.median(lags)) if lags else None,
            "lag_ms_spread": int(max(lags) - min(lags)) if len(lags) > 1 else None}


def analyze(store: SessionStore, sa: str, sb: str, max_ms: int = 1000) -> dict:
    xa, sr = sf.read(str(store.audio_path(sa)), dtype="float32", always_2d=False)
    xb, srb = sf.read(str(store.audio_path(sb)), dtype="float32", always_2d=False)
    if sr != srb:
        raise SystemExit(f"サンプルレートが違う: {sr} / {srb}")
    ma, mb = store.read_meta(sa), store.read_meta(sb)
    if ma.get("t0_unix") is None or mb.get("t0_unix") is None:
        print("[latency_pair] t0_unix が無い（マイクで録ったセッションではない）。壁時計で合わせずに比べる", file=sys.stderr)
        w = 0.0
    else:
        w = float(ma["t0_unix"]) - float(mb["t0_unix"])
    res = {"a": sa, "b": sb, **measure(xa, xb, sr, w, max_ms)}
    store.update_meta(sa, pair={"with": sb, "role": "a", **{k: res[k] for k in ("wall_offset_s", "lag_ms")}})
    store.update_meta(sb, pair={"with": sa, "role": "b", **{k: res[k] for k in ("wall_offset_s", "lag_ms")}})
    (store.dir(sa) / "latency_pair.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def report(res: dict, ma: dict, mb: dict) -> str:
    name = lambda m: m.get("device_name") or m.get("label") or m.get("source") or "?"  # noqa: E731
    lines = [f"A（基準）{res['a']}  {name(ma)}", f"B        {res['b']}  {name(mb)}",
             f"壁時計の差 {res['wall_offset_s'] * 1000:.0f} ms（B の時刻 = A の時刻 + この値）"]
    if not res["claps"]:
        lines.append("叩いた音が見つからない。A の録音を閲覧画面で確かめる")
        return "\n".join(lines)
    lines.append(f"B は A より {res['lag_ms']} ms 遅い（相互相関。山の高さの比 {res['peak_ratio']}）")
    lines.append("叩いた 1 回ずつ：" + "  ".join(f"{c['t_a']}s:{c['lag_ms']}ms" for c in res["claps"]))
    if res["lag_ms_spread"] is not None and res["lag_ms_spread"] > 40:
        lines.append(f"1 回ずつのずれのばらつきが {res['lag_ms_spread']} ms ある。叩く間隔を空けて、もう一度録ると確か")
    lines.append(f"B を入力にするとき：設定に capture.latency_ms: {max(res['lag_ms'], 0)}（A 自身の遅れは含まない）")
    lines.append(f"閲覧画面：python -m intake view → セッション {res['a']} を開くと B が並ぶ（ずらしは壁時計の差）")
    return "\n".join(lines)


def record_pair(cfg, store, dev_a, dev_b, seconds: float, label_a: str, label_b: str) -> tuple[str, str]:
    from intake.__main__ import _pick_device  # noqa: PLC0415
    from intake.capture import MicCapture  # noqa: PLC0415

    base = new_session_id()
    sids, caps = [], []
    for dev, lab in ((dev_a, label_a), (dev_b, label_b)):
        sid = f"{base}_{lab}"
        ns = argparse.Namespace(device=dev, hostapi=None)
        c = cfg["capture"]
        cap = MicCapture(store, sid, sample_rate=cfg["sample_rate"], device=_pick_device(ns, cfg), block_ms=c["block_ms"],
                         latency=c.get("latency"), device_rate=c.get("device_rate"), stall_s=float(c.get("stall_s") or 2.0),
                         extra_meta={"label": lab, "latency_pair": True})
        cap.prepare()
        cap.max_samples = int(seconds * cfg["sample_rate"])
        sids.append(sid)
        caps.append(cap)
    errs: list[BaseException] = []

    def drain(cap):
        try:
            for _ in cap.chunks():
                pass
        except BaseException as e:  # noqa: BLE001
            errs.append(e)
            for c in caps:
                c.stop()

    th = [threading.Thread(target=drain, args=(c,)) for c in caps]
    print(f"[latency_pair] {seconds:.0f} 秒録る。始まったら、2〜3 秒おきに 5 回ほど手を叩く（2 つのマイクの近くで）", file=sys.stderr)
    for t in th:
        t.start()
    try:
        for t in th:
            t.join()
    except KeyboardInterrupt:
        for c in caps:
            c.stop()
        for t in th:
            t.join()
    if errs:
        raise errs[0]
    return sids[0], sids[1]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config")
    ap.add_argument("--a", help="基準の入力機器（PC のマイク）。番号か名前の一部（\"名前@MME\"）")
    ap.add_argument("--b", help="測る入力機器（AirPods など）")
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--label-a", default="pair-a")
    ap.add_argument("--label-b", default="pair-b")
    ap.add_argument("--max-ms", type=int, default=1000, help="探すずれの範囲（±ms）")
    ap.add_argument("--analyze", nargs=2, metavar=("SESSION_A", "SESSION_B"))
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    store = SessionStore(Path(cfg["sessions_dir"]))
    if a.analyze:
        sa, sb = a.analyze
    else:
        if a.b is None:
            ap.error("--b（測る入力機器）が要る。--a を省くと既定の入力機器")
        sa, sb = record_pair(cfg, store, a.a, a.b, a.seconds, a.label_a, a.label_b)
    res = analyze(store, sa, sb, a.max_ms)
    print(report(res, store.read_meta(sa), store.read_meta(sb)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
