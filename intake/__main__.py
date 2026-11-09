"""コマンドの入口。

  python -m intake record   [-c config.yaml] [--device N] [--seconds S]   マイクから録音（同時に各段を流す）
  python -m intake replay   SESSION [-c config.yaml] [--run NAME] [--speed fast|realtime]
  python -m intake import   FILE [--label X] [--replay -c config.yaml]   外部の音声をセッションにする
  python -m intake sessions                                             セッション一覧
  python -m intake devices                                              入力デバイス一覧
  python -m intake view     [-c config.yaml]                            閲覧画面（http://127.0.0.1:8765）
  python -m intake netcheck                                             待ち受けが localhost だけか確認
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path

from .config import load_config
from .session import SessionStore, new_session_id


def _sink(cfg, store: SessionStore, sid: str, run: str):
    from .sink import JsonlSink, MultiSink  # noqa: PLC0415

    sinks = [JsonlSink(store.run_dir(sid, run))] if cfg["sink"].get("jsonl", True) else []
    ws = cfg["sink"].get("websocket") or {}
    if ws.get("enabled"):
        from .sink.websocket import WebSocketSink  # noqa: PLC0415

        sinks.append(WebSocketSink(host=ws["host"], port=int(ws["port"])))
    return MultiSink(sinks)


def _run(cfg, store, sid, run, source, chunks):
    from .pipeline import Pipeline  # noqa: PLC0415

    p = Pipeline(cfg, sid, _sink(cfg, store, sid, run), run_name=run)
    print(f"[intake] session={sid} run={run} vad={p.vad.name} stt={p.stt.name} speaker={p.speaker.name}", file=sys.stderr)
    stats = p.run(chunks, source)
    print(json.dumps({"session": sid, "run": run, **stats}, ensure_ascii=False))
    return stats


def cmd_record(a, cfg, store):
    from .capture import MicCapture  # noqa: PLC0415

    sid = a.session or new_session_id()
    dev = a.device if a.device is not None else cfg["capture"].get("device")
    if isinstance(dev, str) and dev.isdigit():
        dev = int(dev)
    cap = MicCapture(store, sid, sample_rate=cfg["sample_rate"], device=dev, block_ms=cfg["capture"]["block_ms"])
    signal.signal(signal.SIGINT, lambda *_: cap.stop())
    if a.seconds:
        threading.Timer(a.seconds, cap.stop).start()
    print(f"[intake] 録音中… Ctrl+C で止める → {store.audio_path(sid)}", file=sys.stderr)
    _run(cfg, store, sid, a.run or cfg["run_name"], "mic", cap.chunks())


def cmd_replay(a, cfg, store):
    from .replay import WavReplay  # noqa: PLC0415

    speed = a.speed or cfg["replay"]["speed"]
    for sid in a.session:
        rp = WavReplay(store, sid, speed=speed, chunk_ms=cfg["replay"]["chunk_ms"])
        _run(cfg, store, sid, a.run or cfg["run_name"], f"replay:{speed}", rp.chunks())


def cmd_import(a, cfg, store):
    from .replay import import_audio  # noqa: PLC0415

    for f in a.file:
        sid = import_audio(store, f, sample_rate=cfg["sample_rate"], label=a.label)
        print(sid)
        if a.replay:
            a2 = argparse.Namespace(session=[sid], speed=a.speed, run=a.run)
            cmd_replay(a2, cfg, store)


def cmd_sessions(a, cfg, store):  # noqa: ARG001
    for sid in store.list_sessions():
        m = store.read_meta(sid)
        print(f"{sid}\t{m.get('duration', '?')}s\t{m.get('source', '?')}\t{m.get('status', '?')}\truns={','.join(store.list_runs(sid))}")


def cmd_devices(a, cfg, store):  # noqa: ARG001
    from .capture import list_input_devices  # noqa: PLC0415

    for d in list_input_devices():
        print(f"{d['index']}\t{d['name']}\t{d['default_samplerate']:.0f} Hz")


def cmd_view(a, cfg, store):
    from viewer.server import serve  # noqa: PLC0415

    serve(cfg, store, open_browser=not a.no_browser)


def cmd_netcheck(a, cfg, store):  # noqa: ARG001
    from .netcheck import main as nc  # noqa: PLC0415

    raise SystemExit(nc())


def main(argv=None):
    ap = argparse.ArgumentParser(prog="intake")
    ap.add_argument("-c", "--config", help="設定 YAML（省略時は既定値）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="マイクから録音")
    r.add_argument("--device")
    r.add_argument("--seconds", type=float)
    r.add_argument("--session")
    r.add_argument("--run")
    r.set_defaults(fn=cmd_record)

    p = sub.add_parser("replay", help="保存した録音を流し直す")
    p.add_argument("session", nargs="+")
    p.add_argument("--run", help="結果を置く run 名（設定の run_name を上書き）")
    p.add_argument("--speed", choices=["fast", "realtime"])
    p.set_defaults(fn=cmd_replay)

    i = sub.add_parser("import", help="外部の音声ファイルをセッションにする")
    i.add_argument("file", nargs="+")
    i.add_argument("--label")
    i.add_argument("--replay", action="store_true", help="取り込み後に続けて流し直す")
    i.add_argument("--run")
    i.add_argument("--speed", choices=["fast", "realtime"])
    i.set_defaults(fn=cmd_import)

    sub.add_parser("sessions", help="セッション一覧").set_defaults(fn=cmd_sessions)
    sub.add_parser("devices", help="入力デバイス一覧").set_defaults(fn=cmd_devices)
    sub.add_parser("netcheck", help="待ち受けが localhost だけか確かめる").set_defaults(fn=cmd_netcheck)
    v = sub.add_parser("view", help="閲覧画面")
    v.add_argument("--no-browser", action="store_true")
    v.set_defaults(fn=cmd_view)

    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    store = SessionStore(Path(cfg["sessions_dir"]))
    a.fn(a, cfg, store)


if __name__ == "__main__":
    main()
