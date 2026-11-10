"""閲覧画面のサーバ。localhost のみで待ち受ける。外部への送信はしない（CDN も使わない）。"""

from __future__ import annotations

import asyncio
import json
import webbrowser
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from aiohttp import web

from intake.config import LOCAL_HOSTS
from intake.session import SessionStore

STATIC = Path(__file__).parent / "static"
PEAKS_PER_SEC = 200  # 波形の解像度（5 ms ごとに min/max）


def compute_peaks(wav: Path, cache: Path) -> tuple[np.ndarray, int, int]:
    """min/max を int16 で交互に並べた配列。WAV の大きさと mtime が変わったら作り直す。"""
    st = wav.stat()
    key = f"{st.st_size}:{st.st_mtime_ns}:{PEAKS_PER_SEC}"
    keyf = cache.with_suffix(".key")
    if cache.exists() and keyf.exists() and keyf.read_text() == key:
        data = np.fromfile(cache, dtype="<i2")
        info = sf.info(str(wav))
        return data, info.samplerate, info.frames
    info = sf.info(str(wav))
    sr, frames = info.samplerate, info.frames
    step = max(1, sr // PEAKS_PER_SEC)
    n = (frames + step - 1) // step
    out = np.zeros(n * 2, dtype="<i2")
    i = 0
    blk = step * 4000
    with sf.SoundFile(str(wav)) as f:
        while True:
            x = f.read(blk, dtype="int16", always_2d=False)
            if len(x) == 0:
                break
            m = (len(x) + step - 1) // step
            pad = m * step - len(x)
            if pad:
                x = np.concatenate([x, np.full(pad, x[-1], dtype=np.int16)])
            x = x.reshape(m, step)
            out[2 * i : 2 * (i + m) : 2] = x.min(axis=1)
            out[2 * i + 1 : 2 * (i + m) : 2] = x.max(axis=1)
            i += m
    out = out[: 2 * i]
    out.tofile(cache)
    keyf.write_text(key)
    return out, sr, frames


SPEC_NFFT = 512
SPEC_BINS = 128  # 0〜8 kHz（16 kHz のとき）を 128 段に（隣り合う 2 本を平均）
SPEC_DB = (-110.0, -20.0)  # この範囲を 0〜255 に


def compute_spec(wav: Path, t0: float, t1: float, cols: int) -> tuple[np.ndarray, int]:
    """[t0, t1) を cols 列のスペクトログラムにする（列ごとに中心の 32 ms を窓に）。uint8、形は (cols, SPEC_BINS)。

    見ている範囲だけ計算する（拡大・移動のたびに閲覧画面から頼まれる）。長い範囲は列ごとに読み飛ばす。
    """
    info = sf.info(str(wav))
    sr, frames = info.samplerate, info.frames
    cols = int(min(max(cols, 1), 4000))
    t1 = max(t1, t0 + 1e-3)
    centers = ((t0 + (np.arange(cols) + 0.5) * (t1 - t0) / cols) * sr).astype(np.int64)
    half = SPEC_NFFT // 2
    win = np.hanning(SPEC_NFFT)
    norm = 4.0 / (np.sum(win) ** 2)
    a, b = int(centers[0] - half), int(centers[-1] + half)
    with sf.SoundFile(str(wav)) as f:
        if b - a <= 8_000_000:
            lo, hi = max(a, 0), min(b, frames)
            f.seek(lo)
            x = f.read(max(hi - lo, 0), dtype="float32", always_2d=False)
            buf = np.zeros(b - a, dtype=np.float32)
            buf[lo - a: lo - a + len(x)] = x
            idx = (centers - half - a)[:, None] + np.arange(SPEC_NFFT)[None, :]
            seg = buf[idx]
        else:
            seg = np.zeros((cols, SPEC_NFFT), dtype=np.float32)
            for i, c in enumerate(centers):
                s0 = int(c - half)
                if s0 + SPEC_NFFT <= 0 or s0 >= frames:
                    continue
                f.seek(max(s0, 0))
                x = f.read(SPEC_NFFT - max(0, -s0), dtype="float32", always_2d=False)
                seg[i, max(0, -s0): max(0, -s0) + len(x)] = x
    P = (np.abs(np.fft.rfft(seg.astype(np.float64) * win, axis=1)) ** 2) * norm
    P = P[:, : SPEC_BINS * 2].reshape(cols, SPEC_BINS, 2).mean(axis=2)
    db = 10 * np.log10(np.maximum(P, 1e-14))
    lo_db, hi_db = SPEC_DB
    out = np.clip((db - lo_db) / (hi_db - lo_db) * 255, 0, 255).astype(np.uint8)
    out[(centers < 0) | (centers >= frames)] = 0
    return out, sr


def _read_jsonl(p: Path) -> list[dict[str, Any]]:
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass  # 書き込み途中の行
    return rows


def make_app(cfg: dict[str, Any], store: SessionStore) -> web.Application:
    app = web.Application()

    def sid_of(req: web.Request) -> str:
        sid = req.match_info["sid"]
        try:
            d = store.dir(sid)
        except ValueError:
            raise web.HTTPBadRequest(text="bad session") from None
        if not d.exists():
            raise web.HTTPNotFound(text="no session")
        return sid

    async def index(_req):
        return web.FileResponse(STATIC / "index.html")

    async def sessions(_req):
        out = []
        for sid in store.list_sessions():
            runs = []
            group = store.read_meta(sid).get("group")
            for r in store.list_runs(sid):
                rd = store.run_dir(sid, r)
                info = {}
                if (rd / "run.json").exists():
                    info = json.loads((rd / "run.json").read_text(encoding="utf-8"))
                group = group or info.get("group")
                runs.append({"run": r, "providers": info.get("providers"), "started_at": info.get("started_at"),
                             "ref": info.get("ref")})
            dc = store.dir(sid) / "devcheck.json"  # 段11：入力機器の診断
            devcheck = None
            if dc.exists():
                try:
                    devcheck = json.loads(dc.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    pass
            out.append({"session": sid, "meta": store.read_meta(sid), "runs": runs, "group": group, "devcheck": devcheck})
        return web.json_response(out)

    async def align_summary(req):
        """群ごとの整列結果の集計（置換・脱落・挿入の対の数）。数えるだけで、合否は決めない。"""
        run_name = req.query.get("run", "")
        ref = req.query.get("ref", "ref")
        group = req.query.get("group") or None
        unit = req.query.get("unit", "phones")

        def work():
            per, pairs = [], {}
            for sid in store.list_sessions():
                runs = store.list_runs(sid)
                cand = [run_name] if run_name in runs else [r for r in runs if (store.run_dir(sid, r) / "trace" / "phones_align.jsonl").exists()]
                if not cand:
                    continue
                rows = [x for x in _read_jsonl(store.run_dir(sid, cand[0]) / "trace" / "phones_align.jsonl") if x.get("ref_run") == ref]
                if not rows:
                    continue
                g = rows[0].get("group") or store.read_meta(sid).get("group")
                if group and g != group:
                    continue
                cnt = dict.fromkeys(("match", "sub", "del", "ins"), 0)
                for x in rows:
                    ops = x["ops"] if unit == "phones" else x["words"]["ops"]
                    for o in ops:
                        cnt[o["op"]] += 1
                        if o["op"] != "match":
                            k = f"{o.get('ref') or '∅'}→{o.get('hyp') or '∅'}"
                            pairs[k] = pairs.get(k, 0) + 1
                label = store.read_meta(sid).get("label") or store.read_meta(sid).get("original") or ""
                per.append({"session": sid, "run": cand[0], "group": g, "label": label, "counts": cnt})
            top = sorted(pairs.items(), key=lambda kv: -kv[1])[:60]
            return {"ref": ref, "group": group, "unit": unit, "sessions": per, "pairs": top}

        return web.json_response(await asyncio.to_thread(work))

    async def peaks(req):
        sid = sid_of(req)
        wav = store.audio_path(sid)
        if not wav.exists():
            raise web.HTTPNotFound(text="no audio")
        try:
            data, sr, frames = await asyncio.to_thread(compute_peaks, wav, store.dir(sid) / "peaks.bin")
        except RuntimeError as e:  # 録音開始直後でヘッダがまだ無いなど
            raise web.HTTPServiceUnavailable(text=str(e)) from e
        return web.Response(
            body=data.tobytes(),
            content_type="application/octet-stream",
            headers={
                "X-Sample-Rate": str(sr),
                "X-Frames": str(frames),
                "X-Peaks-Per-Sec": str(PEAKS_PER_SEC),
                "Cache-Control": "no-store",
            },
        )

    async def spec(req):
        sid = sid_of(req)
        wav = store.audio_path(sid)
        if not wav.exists():
            raise web.HTTPNotFound(text="no audio")
        try:
            t0, t1 = float(req.query.get("t0", 0)), float(req.query.get("t1", 10))
            cols = int(req.query.get("cols", 800))
        except ValueError:
            raise web.HTTPBadRequest(text="bad range") from None
        try:
            data, sr = await asyncio.to_thread(compute_spec, wav, t0, t1, cols)
        except RuntimeError as e:
            raise web.HTTPServiceUnavailable(text=str(e)) from e
        return web.Response(body=data.tobytes(), content_type="application/octet-stream",
                            headers={"X-Cols": str(data.shape[0]), "X-Bins": str(data.shape[1]), "X-Sample-Rate": str(sr),
                                     "X-Db-Range": f"{SPEC_DB[0]},{SPEC_DB[1]}", "Cache-Control": "no-store"})

    async def audio(req):
        sid = sid_of(req)
        return web.FileResponse(store.audio_path(sid), headers={"Cache-Control": "no-store"})

    async def run(req):
        sid = sid_of(req)
        try:
            rd = store.run_dir(sid, req.match_info["run"])
        except ValueError:
            raise web.HTTPBadRequest(text="bad run") from None
        if not rd.exists():
            raise web.HTTPNotFound(text="no run")
        info = json.loads((rd / "run.json").read_text(encoding="utf-8")) if (rd / "run.json").exists() else {}
        trace = {p.stem: _read_jsonl(p) for p in sorted((rd / "trace").glob("*.jsonl"))} if (rd / "trace").exists() else {}
        return web.json_response({"info": info, "utterances": _read_jsonl(rd / "utterances.jsonl"), "trace": trace})

    app.router.add_get("/", index)
    app.router.add_get("/api/sessions", sessions)
    app.router.add_get("/api/sessions/{sid}/peaks", peaks)
    app.router.add_get("/api/sessions/{sid}/audio", audio)
    app.router.add_get("/api/sessions/{sid}/spec", spec)
    app.router.add_get("/api/sessions/{sid}/runs/{run}", run)
    app.router.add_get("/api/align_summary", align_summary)
    app.router.add_static("/static", STATIC)
    return app


def serve(cfg: dict[str, Any], store: SessionStore, open_browser: bool = True) -> None:
    host, port = cfg["viewer"]["host"], int(cfg["viewer"]["port"])
    if host not in LOCAL_HOSTS:
        raise SystemExit(f"viewer.host={host!r}: localhost 以外では待ち受けない")
    url = f"http://{'127.0.0.1' if host == 'localhost' else host}:{port}/"
    print(f"[viewer] {url}  (sessions_dir={store.root.resolve()})")
    if open_browser:
        webbrowser.open(url)
    web.run_app(make_app(cfg, store), host=host, port=port, print=None)
