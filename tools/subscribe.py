"""発話レコードの購読例（利用側の最小コード）。aiohttp だけで動く。

  python tools/subscribe.py                     # ws://127.0.0.1:8766/ws の発話レコードを 1 行ずつ表示
  python tools/subscribe.py --trace             # VAD 区間・話者分離の区間も受け取る
  python tools/subscribe.py --out records.jsonl # 受け取った発話レコードをファイルにも書く（revise は置き換え）
"""

import argparse
import asyncio
import json

import aiohttp


async def main(url: str, trace: bool, out: str | None) -> None:
    records: dict[str, dict] = {}
    async with aiohttp.ClientSession() as cs:
        async with cs.ws_connect(url + ("?trace=1" if trace else "")) as ws:
            async for msg in ws:
                m = json.loads(msg.data)
                t = m["type"]
                if t == "hello":
                    i = m["info"]
                    print(f"# session={i['session']} run={i['run']} providers={i.get('providers')}")
                    records.clear()
                elif t == "utterance":
                    r = m["record"]
                    records[r["id"]] = r
                    print(f"{r['id']} {r['t0']:8.2f}-{r['t1']:8.2f} {r.get('speaker') or '-':>10} {r['text']}")
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
    ap.add_argument("--out")
    a = ap.parse_args()
    try:
        asyncio.run(main(a.url, a.trace, a.out))
    except KeyboardInterrupt:
        pass
