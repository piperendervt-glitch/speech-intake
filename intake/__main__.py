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


_WS = None


def _ws(cfg):
    """WebSocket 配信は 1 回のコマンドで 1 つだけ立てる（複数セッションを流しても同じ口で配る）。"""
    global _WS
    ws = cfg["sink"].get("websocket") or {}
    if not ws.get("enabled"):
        return None
    if _WS is None:
        from .sink.websocket import WebSocketSink  # noqa: PLC0415

        _WS = WebSocketSink(host=ws["host"], port=int(ws["port"]))
        print(f"[intake] 配信中 ws://{ws['host']}:{ws['port']}/ws", file=sys.stderr)
    return _WS


def _run(cfg, store, sid, run, source, chunks):
    from .pipeline import Pipeline  # noqa: PLC0415
    from .sink import JsonlSink, MultiSink  # noqa: PLC0415

    sinks = [JsonlSink(store.run_dir(sid, run))] if cfg["sink"].get("jsonl", True) else []
    ws = _ws(cfg)
    if ws:
        sinks.append(ws)
    cfg = {**cfg, "_session_dir": str(store.dir(sid))}
    p = Pipeline(cfg, sid, MultiSink(sinks), run_name=run)
    if ws:  # 購読側から届く装着者区間などを speaker provider に渡す
        ws.on_message = getattr(p.speaker, "on_message", None)
    print(f"[intake] session={sid} run={run} vad={p.vad.name} stt={p.stt.name} speaker={p.speaker.name}", file=sys.stderr)
    stats = p.run(chunks, source)
    print(json.dumps({"session": sid, "run": run, **stats}, ensure_ascii=False))
    return stats


def _hold(a):
    if _WS is not None and getattr(a, "hold", False):
        print("[intake] 配信を続けている。Ctrl+C で終わる", file=sys.stderr)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
    if _WS is not None:
        _WS.shutdown()


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
    signal.signal(signal.SIGINT, signal.default_int_handler)
    _hold(a)


def _replay_one(a, cfg, store, sid):
    from .replay import WavReplay  # noqa: PLC0415

    speed = a.speed or cfg["replay"]["speed"]
    rp = WavReplay(store, sid, speed=speed, chunk_ms=cfg["replay"]["chunk_ms"])
    _run(cfg, store, sid, a.run or cfg["run_name"], f"replay:{speed}", rp.chunks())


def cmd_replay(a, cfg, store):
    for sid in a.session:
        _replay_one(a, cfg, store, sid)
    _hold(a)


def cmd_import(a, cfg, store):
    from .replay import import_audio  # noqa: PLC0415

    for f in a.file:
        sid = import_audio(store, f, sample_rate=cfg["sample_rate"], label=a.label)
        print(sid)
        if a.replay:
            _replay_one(a, cfg, store, sid)
    _hold(a)


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
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="設定の一部を上書き（例 --set vad.min_silence_ms=800）。何度でも指定できる")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="マイクから録音")
    r.add_argument("--device")
    r.add_argument("--seconds", type=float)
    r.add_argument("--session")
    r.add_argument("--run")
    r.add_argument("--hold", action="store_true", help="終わった後も WebSocket 配信を続ける")
    r.set_defaults(fn=cmd_record)

    p = sub.add_parser("replay", help="保存した録音を流し直す")
    p.add_argument("session", nargs="+")
    p.add_argument("--run", help="結果を置く run 名（設定の run_name を上書き）")
    p.add_argument("--speed", choices=["fast", "realtime"])
    p.add_argument("--hold", action="store_true", help="終わった後も WebSocket 配信を続ける")
    p.set_defaults(fn=cmd_replay)

    i = sub.add_parser("import", help="外部の音声ファイルをセッションにする")
    i.add_argument("file", nargs="+")
    i.add_argument("--label")
    i.add_argument("--replay", action="store_true", help="取り込み後に続けて流し直す")
    i.add_argument("--run")
    i.add_argument("--speed", choices=["fast", "realtime"])
    i.add_argument("--hold", action="store_true", help="終わった後も WebSocket 配信を続ける")
    i.set_defaults(fn=cmd_import)

    sub.add_parser("sessions", help="セッション一覧").set_defaults(fn=cmd_sessions)
    sub.add_parser("devices", help="入力デバイス一覧").set_defaults(fn=cmd_devices)
    sub.add_parser("netcheck", help="待ち受けが localhost だけか確かめる").set_defaults(fn=cmd_netcheck)
    v = sub.add_parser("view", help="閲覧画面")
    v.add_argument("--no-browser", action="store_true")
    v.set_defaults(fn=cmd_view)

    a = ap.parse_args(argv)
    cfg = load_config(a.config, a.set)
    store = SessionStore(Path(cfg["sessions_dir"]))
    a.fn(a, cfg, store)


if __name__ == "__main__":
    main()
