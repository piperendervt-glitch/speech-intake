"""発話レコードの購読例（利用側の最小コード）。aiohttp だけで動く。

  python tools/subscribe.py                     # ws://127.0.0.1:8766/ws の発話レコードを 1 行ずつ表示
  python tools/subscribe.py --events            # 発話の始まり・終わりの通知（speech_start / speech_end / speech_dropped）も時刻つきで出す
  python tools/subscribe.py --trace             # VAD 区間・話者分離の区間も受け取る
  python tools/subscribe.py --out records.jsonl # 受け取った発話レコードをファイルにも書く（revise は置き換え）

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


async def main(url: str, trace: bool, out: str | None, events: bool) -> None:
    records: dict[str, dict] = {}
    async with aiohttp.ClientSession() as cs:
        async with cs.ws_connect(url + ("?trace=1" if trace else "")) as ws:
            async for msg in ws:
                now = time.time()
                m = json.loads(msg.data)
                t = m["type"]
                if t == "hello":
                    i = m["info"]
                    print(f"# session={i['session']} run={i['run']} providers={i.get('providers')}")
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
                    print(f"{stamp(now, m.get('t_unix'))} {t:<14} {body}")
                elif t == "utterance":
                    r = m["record"]
                    records[r["id"]] = r
                    line = f"{r['id']} {r['t0']:8.2f}-{r['t1']:8.2f} {r.get('speaker') or '-':>10} {r['text']}"
                    if events:
                        tm = m.get("timing") or {}
                        stt = f"  STT {(tm['stt_done_unix'] - tm['vad_close_unix']) * 1000:.0f}ms" if tm else ""
                        line = f"{stamp(now, tm.get('stt_done_unix'))} {'utterance':<14} seg={m.get('seg', '-')!s:<4}{stt}  {line}"
                    print(line)
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
    a = ap.parse_args()
    try:
        asyncio.run(main(a.url, a.trace, a.out, a.events))
    except KeyboardInterrupt:
        pass
