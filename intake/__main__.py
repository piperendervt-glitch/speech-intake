"""コマンドの入口。

  python -m intake record   [-c config.yaml] [--device N] [--seconds S]   マイクから録音（同時に各段を流す）
  python -m intake record   --input ja1 [-c config.yaml]                 JA1 行の UDP（AirPods など）から録音
  python -m intake replay   SESSION [-c config.yaml] [--run NAME] [--speed fast|realtime]
  python -m intake import   FILE [--label X] [--replay -c config.yaml]   外部の音声をセッションにする
  python -m intake sessions                                             セッション一覧
  python -m intake devices                                              入力デバイス一覧
  python -m intake view     [-c config.yaml]                            閲覧画面（http://127.0.0.1:8765）
  python -m intake models   speaker|whisper|phones|all                  モデルを取得
  python -m intake align    SESSION [--run R] [--ref ref ref_g2p]       参照 run と音素を整列（trace/phones_align.jsonl）
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
    if ws:  # 購読側から届く set（VAD の設定変更）や装着者区間を渡す
        ws.on_message = p.on_message
    print(f"[intake] session={sid} run={run} vad={p.vad.name} stt={p.stt.name} speaker={p.speaker.name}", file=sys.stderr)
    stats = p.run(chunks, source)
    print(json.dumps({"session": sid, "run": run, **stats}, ensure_ascii=False))
    if p.phones.name != "none":  # 参照 run（ref / ref_g2p）があれば整列しておく
        from .phones.align import align_session  # noqa: PLC0415

        for out in align_session(store.dir(sid), runs=[run], table_path=cfg.get("phones", {}).get("classes")):
            print(f"[intake] 整列: {out}", file=sys.stderr)
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
    sid = a.session or new_session_id()
    prov = a.input or cfg["capture"].get("provider", "sounddevice")
    if prov in ("ja1_udp", "ja1"):  # subvocal-robot の airpods.bat などが送る JA1 行（段11）
        from .capture.ja1 import Ja1UdpCapture  # noqa: PLC0415

        udp = cfg["capture"].get("udp") or {}
        cap = Ja1UdpCapture(store, sid, sample_rate=cfg["sample_rate"], host=udp.get("host", "127.0.0.1"),
                            port=int(udp.get("port", 9750)), source=udp.get("source"))
        source = "ja1_udp"
        print(f"[intake] JA1 を待ち受け中 udp://{cap.host}:{cap.port}", file=sys.stderr)
    elif prov in ("sounddevice", "mic"):
        from .capture import MicCapture  # noqa: PLC0415

        dev = a.device if a.device is not None else cfg["capture"].get("device")
        if isinstance(dev, str) and dev.isdigit():
            dev = int(dev)
        cap = MicCapture(store, sid, sample_rate=cfg["sample_rate"], device=dev, block_ms=cfg["capture"]["block_ms"],
                         latency=cfg["capture"].get("latency"))
        source = "mic"
    else:
        raise SystemExit(f"capture.provider={prov!r} は未登録。候補: sounddevice / ja1_udp")
    cap.prepare()  # run の出力（runs/<run>/）より先にセッションを作る
    signal.signal(signal.SIGINT, lambda *_: cap.stop())
    if a.seconds:  # 録音した長さで止める（モデルの読み込みの時間は数えない）
        cap.max_samples = int(a.seconds * cfg["sample_rate"])
    print(f"[intake] 録音中… Ctrl+C で止める → {store.audio_path(sid)}", file=sys.stderr)
    _run(cfg, store, sid, a.run or cfg["run_name"], source, cap.chunks())
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


def cmd_models(a, cfg, store):  # noqa: ARG001
    from .models import fetch_phones, fetch_speaker, fetch_whisper  # noqa: PLC0415

    root = Path(a.dir)
    if a.what in ("speaker", "all"):
        fetch_speaker(root)
    if a.what in ("whisper", "all"):
        fetch_whisper(a.whisper, root)
    if a.what in ("phones", "all"):
        fetch_phones(cfg.get("phones") or {}, root)


def cmd_align(a, cfg, store):
    from .phones.align import align_session  # noqa: PLC0415

    for sid in a.session:
        outs = align_session(store.dir(sid), runs=a.run, refs=a.ref, table_path=a.table or (cfg.get("phones") or {}).get("classes"))
        if not outs:
            print(f"{sid}: 整列するもの（音素つきの run と参照 run）が無い", file=sys.stderr)
        for o in outs:
            print(o)


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
    r.add_argument("--input", choices=["mic", "ja1"], help="入口（省略時は設定の capture.provider）。ja1 = JA1 行の UDP（AirPods など）")
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
    m = sub.add_parser("models", help="モデルを取得する（models/ に置く）")
    m.add_argument("what", choices=["speaker", "whisper", "phones", "all"])
    m.add_argument("--whisper", default="large-v3")
    m.add_argument("--dir", default="models")
    m.set_defaults(fn=cmd_models)
    al = sub.add_parser("align", help="参照 run（ref / ref_g2p）と音素つき run を整列する")
    al.add_argument("session", nargs="+")
    al.add_argument("--run", nargs="*", help="音素つきの run（省略時はすべて）")
    al.add_argument("--ref", nargs="*", help="参照 run（省略時はすべて）")
    al.add_argument("--table", help="粗い音素クラスの表（省略時は intake/phones/classes.yaml）")
    al.set_defaults(fn=cmd_align)
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
