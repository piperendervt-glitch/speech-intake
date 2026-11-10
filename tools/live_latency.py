"""run の trace/events.jsonl から、区間ごとの通知の遅れを並べる（段10。REPORT の数字はこれで出した）。

  python tools/live_latency.py SESSION [--run live] [--sessions sessions]

列（ms）:
  start_confirm  話し始め（t）から始まりを確定するまで（音声の時計。VAD の枠と start_frames で決まる）
  start_wall     確定してから speech_start を配るまで（壁時計。ブロックの待ち・処理の遅れ。実時間の run だけ）
  end_confirm    区間の終わり（t1、余白込み）から speech_end を確定するまで（音声の時計。min_silence_ms で決まる）
  stt            speech_end から STT が終わるまで（timing.stt_done_unix - timing.vad_close_unix）
  utt_after_end  speech_end から utterance を配るまで（音素などの後処理も含む）
合否は決めない。最後に中央値と最大を出す。
"""

import argparse
import json
import statistics
from pathlib import Path


def rows(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def table(ev: list[dict]) -> list[dict]:
    segs: dict[int, dict] = {}
    for e in ev:
        if e.get("seg") is None:
            continue
        g = segs.setdefault(e["seg"], {"seg": e["seg"]})
        if e["type"] == "speech_start":
            g["t"] = e["t"]
            g["start_confirm"] = (e["t_detect"] - e["t"]) * 1000
            if "t_emit" in e:
                g["start_wall"] = (e["t_emit"] - e["t_detect"]) * 1000
        elif e["type"] == "speech_end" and not e.get("dropped"):
            g["end_confirm"] = (e["t_detect"] - e["t1"]) * 1000
        elif e["type"] == "speech_dropped":
            g["result"] = f"dropped:{e['reason']}"
        elif e["type"] == "partial" and "partial_unix" not in g:
            g["partial_unix"] = e["t_unix"]
        elif e["type"] == "utterance" and "stt" not in g:
            g["stt"] = (e["stt_done_unix"] - e["vad_close_unix"]) * 1000
            g["utt_after_end"] = (e["t_unix"] - e["vad_close_unix"]) * 1000
            g["result"] = e["id"]
            if "partial_unix" in g:  # 最初の暫定の文字が、確定の utterance よりどれだけ先に出たか（段11）
                g["partial_lead"] = (e["t_unix"] - g["partial_unix"]) * 1000
    for g in segs.values():
        g.pop("partial_unix", None)
    return [segs[k] for k in sorted(segs)]


COLS = ("start_confirm", "start_wall", "end_confirm", "stt", "utt_after_end", "partial_lead")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--run", default="live")
    ap.add_argument("--sessions", default="sessions")
    ap.add_argument("--json", action="store_true", help="区間ごとの表を JSON で出す")
    a = ap.parse_args()
    path = Path(a.sessions) / a.session / "runs" / a.run / "trace" / "events.jsonl"
    if not path.exists():
        print(f"{path} が無い（VAD 区間が 1 つも無かったか、段10 より前の run）")
        return
    tab = table(rows(path))
    if a.json:
        print(json.dumps(tab, ensure_ascii=False, indent=1))
        return
    print("seg      t  " + "  ".join(f"{c:>13}" for c in COLS) + "  result")
    for g in tab:
        cells = "  ".join(f"{g[c]:13.0f}" if c in g else f"{'':>13}" for c in COLS)
        print(f"{g['seg']:>3} {g.get('t', 0):7.2f}  {cells}  {g.get('result', '')}")
    print()
    for c in COLS:
        v = [g[c] for g in tab if c in g]
        if v:
            print(f"{c:>13}: n={len(v):3d}  中央値 {statistics.median(v):6.0f} ms  最大 {max(v):6.0f} ms  最小 {min(v):6.0f} ms")


if __name__ == "__main__":
    main()
