"""コマンドの入口。

  python -m intake record   [-c config.yaml] [--device N] [--seconds S]   マイクから録音（同時に各段を流す）
  python -m intake record   --input ja1 [-c config.yaml]                 JA1 行の UDP（AirPods など）から録音
  python -m intake replay   SESSION [-c config.yaml] [--run NAME] [--speed fast|realtime]
  python -m intake import   FILE [--label X] [--replay -c config.yaml]   外部の音声をセッションにする
  python -m intake sessions                                             セッション一覧
  python -m intake devices  [--json]                                     入力機器の一覧（ホスト API・既定のレート・開けるか・BT）
  python -m intake devcheck [--device 名前] [--seconds 8]               入力機器の診断（帯域・頭の欠け・雑音・途切れ。段11）
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


def _pick_device(a, cfg):
    """--device / capture.device（番号・名前の一部）を番号にする。複数当たったら候補を出して止まる（段11）。"""
    from .capture import list_input_devices, resolve_device  # noqa: PLC0415

    spec = getattr(a, "device", None)
    if spec is None:
        spec = cfg["capture"].get("device")
    api = getattr(a, "hostapi", None) or cfg["capture"].get("hostapi")
    if spec is None or (isinstance(spec, int) and not api):
        return spec
    try:
        return resolve_device(spec, list_input_devices(probe_rate=None), hostapi=api)
    except ValueError as e:
        raise SystemExit(f"[intake] {e}") from None


def _mic(a, cfg, store, sid, **kw):
    from .capture import MicCapture  # noqa: PLC0415

    c = cfg["capture"]
    return MicCapture(store, sid, sample_rate=cfg["sample_rate"], device=_pick_device(a, cfg), block_ms=c["block_ms"],
                      latency=c.get("latency"), device_rate=kw.pop("device_rate", c.get("device_rate")),
                      stall_s=float(c.get("stall_s") or 2.0),
                      extra_meta={"latency_ms": c.get("latency_ms") or 0, **kw.pop("extra_meta", {})}, **kw)


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

        cap = _mic(a, cfg, store, sid)
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
    if getattr(cap, "lost", None):
        print(f"[intake] 機器が途中で消えたため止めた：{cap.lost}", file=sys.stderr)
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

    rows = list_input_devices(probe_rate=None if a.no_probe else cfg["sample_rate"])
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    print(format_devices(rows, cfg["sample_rate"]))


_OPEN_MARK = {"ok": "○", "native": "△", "ng": "×"}


def format_devices(rows, sr=16000) -> str:
    """devices の表。印：○ 16 kHz で開ける／△ 機器の既定のレートでなら開ける（取り込みで 16 kHz に直す）／× 開けない。
    BT = 名前から Bluetooth のヘッドセットらしい行。Hands-Free（ハンズフリー）の行がマイク。"""
    out = [f"番号\t開\tBT\tAPI\t既定 Hz\tch\t名前"]
    for d in rows:
        mark = _OPEN_MARK.get(d.get("open"), "-")
        out.append(f"{d['index']}\t{mark}\t{'BT' if d.get('bt') else ''}\t{d['hostapi']}\t{d['default_samplerate']:.0f}\t{d['channels']}\t{d['name']}")
    out.append("")
    out.append(f"開：○ {sr} Hz で開ける / △ 既定のレートでなら開ける（16 kHz に直して保存）/ × 開けない / - 確かめていない")
    if any(d.get("bt") for d in rows):
        out.append("BT：名前から Bluetooth らしい行。マイクとして使えるのは Hands-Free（ハンズフリー）/ Headset の行。"
                   "番号は抜き差しで変わるので capture.device には名前の一部（例 \"AirPods@MME\"）を書く")
    out.append("次に：python -m intake devcheck --device \"名前の一部@API\" で帯域を確かめる")
    return "\n".join(out)


def cmd_devcheck(a, cfg, store):
    from .devcheck import analyze_session, format_report, SENTENCES  # noqa: PLC0415

    if a.analyze:
        for sid in a.analyze:
            rep = analyze_session(store, sid)
            print(format_report(rep))
        return
    rate = a.rate or "native"
    rate = None if rate == "auto" else rate
    label = a.label or "devcheck"
    sid = new_session_id(suffix=label)
    cap = _mic(a, cfg, store, sid, device_rate=rate, extra_meta={"devcheck": True, "label": label})
    cap.prepare()
    cap.max_samples = int(a.seconds * cfg["sample_rate"])
    signal.signal(signal.SIGINT, lambda *_: cap.stop())
    dev = store.read_meta(sid)
    print(f"[devcheck] 機器：{dev.get('device_name', dev.get('device'))}（{dev.get('hostapi', '?')}、既定 {dev.get('device_default_rate', '?')} Hz）",
          file=sys.stderr)
    print(f"[devcheck] {a.seconds:.0f} 秒録る。始まったら 1 秒ほど黙ってから、次の英文をはっきり読む（s・sh・th の音を見る）：\n", file=sys.stderr)
    for s in SENTENCES:
        print(f"    {s}", file=sys.stderr)
    print("", file=sys.stderr)
    for _ in cap.chunks():
        pass
    signal.signal(signal.SIGINT, signal.default_int_handler)
    rep = analyze_session(store, sid)
    print(format_report(rep))
    print(f"[devcheck] 閲覧画面（python -m intake view）でセッション {sid} を選び、「スペクトログラム」で 4 kHz より上を目で確かめる",
          file=sys.stderr)


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
    r.add_argument("--device", help="番号か名前の一部（\"AirPods@MME\" のように @ でホスト API も）")
    r.add_argument("--hostapi", help="名前で選ぶときのホスト API（MME / WASAPI など）")
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
    dv = sub.add_parser("devices", help="入力デバイス一覧（ホスト API・既定のレート・開けるか）")
    dv.add_argument("--json", action="store_true")
    dv.add_argument("--no-probe", action="store_true", help="開けるかを確かめない")
    dv.set_defaults(fn=cmd_devices)
    dc = sub.add_parser("devcheck", help="入力機器の診断：数秒録って帯域・頭の欠け・雑音・途切れを見る（段11）")
    dc.add_argument("--device", help="番号か名前の一部（\"AirPods@MME\"）")
    dc.add_argument("--hostapi")
    dc.add_argument("--seconds", type=float, default=8.0)
    dc.add_argument("--rate", help="機器を開くレート：native（既定）/ auto（16 kHz で開けたらそれ）/ 数")
    dc.add_argument("--label", help="セッション名の後ろに付ける（英数字と -。例 airpods）")
    dc.add_argument("--analyze", nargs="+", metavar="SESSION", help="録らずに、既にあるセッションを診断する")
    dc.set_defaults(fn=cmd_devcheck)
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
