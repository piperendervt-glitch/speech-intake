"""発話レコードの購読例（利用側の最小コード）。aiohttp だけで動く。

  python tools/subscribe.py                     # ws://127.0.0.1:8766/ws の発話レコードを 1 行ずつ表示
  python tools/subscribe.py --events            # 発話の始まり・終わりの通知（speech_start / speech_end / speech_dropped）も時刻つきで出す
  python tools/subscribe.py --trace             # VAD 区間・話者分離の区間も受け取る
  python tools/subscribe.py --out records.jsonl # 受け取った発話レコードをファイルにも書く（revise は置き換え）
  python tools/subscribe.py --events --min-silence-ms 200   # 繋いだときに VAD の min_silence_ms を変える（段11。全員に効く）

暫定の文字（partial。段11、pipeline.partial を設定したときだけ）は --events で出る。

speech-intake がまだ起動していない・止まったときは、1 秒ごとに繋ぎ直す（Ctrl+C で終わる）。

--events の各行の先頭は受け取った時刻（壁時計）。+ms は、その通知を speech-intake が出した時刻（t_unix）から
受け取るまでの遅れ（同じ PC なら 1 ms 前後）。utterance の行には、区間が閉じてから STT が終わるまでの時間も出す。
"""

import argparse
import asyncio
import json
import time
from datetime import datetime

import aiohttp


def stamp(now: float, sent: float | None) -> str:
    s = datetime.fromtimestamp(now).strftime("%H:%M:%S.%f")[:-3]
    return f"{s} (+{(now - sent) * 1000:4.0f}ms)" if sent else f"{s}         "


async def main(url: str, trace: bool, out: str | None, events: bool, sets: dict | None = None) -> None:
    waiting = False
    while True:
        try:
            await subscribe(url, trace, out, events, sets)
            waiting = False
        except aiohttp.ClientConnectionError:
            if not waiting:
                print(f"# {url} に繋がらない。待っている…", flush=True)
                waiting = True
        await asyncio.sleep(1)


async def subscribe(url: str, trace: bool, out: str | None, events: bool, sets: dict | None = None) -> None:
    records: dict[str, dict] = {}
    async with aiohttp.ClientSession() as cs:
        async with cs.ws_connect(url + ("?trace=1" if trace else "")) as ws:
            print(f"# 接続 {url}", flush=True)
            if sets:
                await ws.send_str(json.dumps({"type": "set", "vad": sets}))
            async for msg in ws:
                now = time.time()
                m = json.loads(msg.data)
                t = m["type"]
                if t == "hello":
                    i = m["info"]
                    print(f"# session={i['session']} run={i['run']} providers={i.get('providers')} dir={i.get('session_dir')}")
                    records.clear()
                elif t in ("speech_start", "speech_end", "speech_dropped"):
                    if not events:
                        continue
                    if t == "speech_start":
                        body = f"seg={m['seg']:<4} t={m['t']:8.2f}  確定 {m['t_detect']:8.2f}  p={m['p']:.2f}"
                    elif t == "speech_end":
                        body = f"seg={m['seg']:<4} {m['t0']:8.2f}-{m['t1']:8.2f}  確定 {m['t_detect']:8.2f}" + (f"  捨てた: {m['dropped']}" if m.get("dropped") else "")
                    else:
                        body = f"seg={m['seg']:<4} {m['reason']}"
                    print(f"{stamp(now, m.get('t_unix'))} {t:<14} {body}", flush=True)
                elif t == "partial":
                    if events:
                        body = f"seg={m['seg']:<4} {m['t0']:8.2f}-{m['t1']:8.2f}  {m['reason']:<8} STT {m['stt_ms']}ms  {m['text']}"
                        print(f"{stamp(now, m.get('t_unix'))} {t:<14} {body}", flush=True)
                elif t == "config":
                    print(f"# config {m['vad']}（変更 {m['changed']}）", flush=True)
                elif t == "error":
                    print(f"# error {m['error']}", flush=True)
                elif t == "utterance":
                    r = m["record"]
                    records[r["id"]] = r
                    line = f"{r['id']} {r['t0']:8.2f}-{r['t1']:8.2f} {r.get('speaker') or '-':>10} {r['text']}"
                    if events:
                        tm = m.get("timing") or {}
                        stt = f"  STT {(tm['stt_done_unix'] - tm['vad_close_unix']) * 1000:.0f}ms" if tm else ""
                        line = f"{stamp(now, tm.get('stt_done_unix'))} {'utterance':<14} seg={m.get('seg', '-')!s:<4}{stt}  {line}"
                    print(line, flush=True)
                elif t == "revise":
                    records = {r["id"]: r for r in m["records"]}
                    print(f"# revise: {len(records)} 件を付け直し（話者など）")
                elif t == "trace":
                    print(f"  [{m['stage']}] {m['row']}")
                elif t == "end":
                    print(f"# end {m['stats']}")
                if out and t in ("utterance", "revise", "end"):
                    with open(out, "w", encoding="utf-8") as f:
                        for r in sorted(records.values(), key=lambda r: r["t0"]):
                            f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:8766/ws")
    ap.add_argument("--trace", action="store_true")
    ap.add_argument("--events", action="store_true", help="speech_start / speech_end / speech_dropped を時刻つきで出す")
    ap.add_argument("--out")
    ap.add_argument("--min-silence-ms", type=int, help="繋いだときに vad.min_silence_ms を変える（speech-intake 全体に効く）")
    ap.add_argument("--start-frames", type=int, help="繋いだときに vad.start_frames を変える")
    a = ap.parse_args()
    sets = {k: v for k, v in (("min_silence_ms", a.min_silence_ms), ("start_frames", a.start_frames)) if v is not None}
    try:
        asyncio.run(main(a.url, a.trace, a.out, a.events, sets))
    except KeyboardInterrupt:
        pass
