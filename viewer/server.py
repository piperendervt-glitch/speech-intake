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
            for r in store.list_runs(sid):
                rd = store.run_dir(sid, r)
                info = {}
                if (rd / "run.json").exists():
                    info = json.loads((rd / "run.json").read_text(encoding="utf-8"))
                runs.append({"run": r, "providers": info.get("providers"), "started_at": info.get("started_at")})
            out.append({"session": sid, "meta": store.read_meta(sid), "runs": runs})
        return web.json_response(out)

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
    app.router.add_get("/api/sessions/{sid}/runs/{run}", run)
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
