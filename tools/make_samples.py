"""確認用の 2 話者会話を TTS で合成して samples/ に置く。

  python tools/make_samples.py --engine kokoro   # 既定。自然な声（要: pip install "kokoro>=0.9" "misaki[ja]"、初回にモデルを取得）
  python tools/make_samples.py --engine espeak   # 機械的な声（要: espeak-ng）。開発中の動作確認用
  python tools/make_samples.py --name conv5_ja --speakers 3   # Kokoro の 5 種類の声で 3〜5 人の打ち合わせ
  python tools/make_samples.py --name en_read      # 英語の読み上げ（段6・7。要: pip install "misaki[en]"）
  python tools/make_samples.py --name en_misread   # 同じ文を、わざと誤読させて読む（rice→lice など。text は本来の文）

出力:
  samples/<name>.wav          16 kHz mono
  samples/<name>.script.json  台本と、各台詞を置いた時刻（合成時に分かっている正解。比較の目安）
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16000

# 会話の台本: (話者, 台詞, 直前の間[秒])。間がマイナスなら前の台詞に重ねる。
SCRIPTS = {
    "conv2_ja": [
        ("A", "おはようございます。先月の売上の件で、少し相談してもいいですか。", 0.6),
        ("B", "はい、どうぞ。何か気になるところがありましたか。", 0.5),
        ("A", "未入金の得意先がいくつかあるみたいなんです。一覧を出してもらえますか。", 0.4),
        ("B", "わかりました。先月分の未入金ですね。", 0.7),
        ("B", "三社あります。いちばん大きいのは山田商事さんです。", 1.2),
        ("A", "山田商事さんは、先々月も遅れていませんでしたか。", 0.3),
        ("B", "そうですね、二か月続けてです。", 0.25),
        ("A", "では、今日中に担当者へ連絡をお願いします。", 0.5),
        ("B", "承知しました。", 0.4),
    ],
    # 5 人の打ち合わせ（Kokoro の日本語の声 5 種類。--speakers で 3〜5 人に絞れる）
    "conv5_ja": [
        ("A", "では、来週の展示会の準備について確認しましょう。", 0.6),
        ("B", "はい。ブースの配置図は昨日できあがりました。", 0.5),
        ("C", "入口の近くに試作機を置くんですよね。", 0.4),
        ("B", "そうです。電源の場所だけ、まだ決まっていません。", 0.3),
        ("D", "電源なら、会場の担当の方に聞いておきます。", 0.6),
        ("A", "ありがとうございます。配布する資料はどうなっていますか。", 0.5),
        ("E", "資料は三百部、金曜日に届く予定です。", 0.7),
        ("C", "少し足りない気がします。去年は四百部配りました。", 0.3),
        ("E", "では、追加で百部頼んでおきます。", 0.4),
        ("D", "当日の受付は、私と田中さんの二人で大丈夫でしょうか。", 0.8),
        ("A", "午前中は混むので、もう一人いた方がいいですね。", 0.4),
        ("B", "私が午前だけ手伝います。", 0.25),
        ("C", "試作機の説明は私がやります。", 0.5),
        ("E", "名札と飲み物の手配も、まとめて私がやっておきます。", 0.6),
        ("A", "それでは、金曜日にもう一度集まりましょう。", 0.5),
        ("D", "わかりました。", 0.3),
    ],
}

# 英語の読み上げ（段6・7）。(本来の文, わざと誤読させた読み)。誤読は日本語話者に多いと言われる対を選んだ
EN_LINES = [
    ("I would like some rice with my dinner.", "I would like some lice with my dinner."),
    ("The light in the hall is very bright.", "The right in the hall is berry bright."),
    ("I think the sea is calm today.", "I sink the she is calm today."),
    ("Please read the first line of the letter.", "Please lead the first line of the retter."),
    ("She works at the bank near the park.", "She walks at the bank near the pork."),
    ("Thank you for the beautiful flowers.", "Sank you for the beautiful flowers."),
]
SCRIPTS["en_read"] = [("A", t, 0.6, t) for t, _ in EN_LINES]
SCRIPTS["en_misread"] = [("A", t, 0.6, m) for t, m in EN_LINES]
LANG = {"en_read": "a", "en_misread": "a"}  # Kokoro の言語（a = 米国英語）。無ければ日本語

KOKORO_VOICES = {"A": "jf_alpha", "B": "jm_kumo", "C": "jf_gongitsune", "D": "jf_nezumi", "E": "jf_tebukuro"}
KOKORO_VOICES_EN = {"A": "af_heart", "B": "am_michael"}
ESPEAK_VOICES = {"A": ("ja", 70), "B": ("ja", 25)}  # (voice, pitch)
ESPEAK_VOICES_EN = {"A": ("en-us", 50), "B": ("en-us", 25)}


def _resample(x: np.ndarray, sr: int) -> np.ndarray:
    if sr == SR:
        return x.astype(np.float32)
    if shutil.which("ffmpeg"):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.wav", Path(d) / "b.wav"
            sf.write(a, x, sr)
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(a), "-ar", str(SR), str(b)], check=True)
            return sf.read(b, dtype="float32")[0]
    n = int(len(x) * SR / sr)
    return np.interp(np.linspace(0, len(x) - 1, n), np.arange(len(x)), x).astype(np.float32)


def tts_espeak(spk: str, text: str, lang: str = "j") -> np.ndarray:
    voice, pitch = (ESPEAK_VOICES_EN if lang == "a" else ESPEAK_VOICES)[spk]
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "o.wav"
        subprocess.run(["espeak-ng", "-v", voice, "-p", str(pitch), "-s", "150", "-w", str(p), text], check=True)
        x, sr = sf.read(p, dtype="float32")
    return _resample(x, sr)


_kpipe: dict = {}


def tts_kokoro(spk: str, text: str, lang: str = "j") -> np.ndarray:
    if lang not in _kpipe:
        from kokoro import KPipeline  # noqa: PLC0415

        _kpipe[lang] = KPipeline(lang_code=lang)
    voice = (KOKORO_VOICES_EN if lang == "a" else KOKORO_VOICES)[spk]
    parts = [a.cpu().numpy() if hasattr(a, "cpu") else np.asarray(a) for _, _, a in _kpipe[lang](text, voice=voice)]
    return _resample(np.concatenate(parts), 24000)


def build(name: str, engine: str, out: Path, speakers: int | None = None) -> None:
    tts = tts_kokoro if engine == "kokoro" else tts_espeak
    track = np.zeros(0, dtype=np.float32)
    cursor = 0.0
    lines = []
    lang = LANG.get(name, "j")
    script = [(x[0], x[1], x[2], x[3] if len(x) > 3 else x[1]) for x in SCRIPTS[name]]  # (話者, 本来の文, 間, 読ませる文)
    if speakers:  # 先頭から speakers 人だけを残す（それ以外の人の台詞は、残る人に順に振り直す）
        keep = sorted({s for s, _, _ in script})[:speakers]
        script = [(s if s in keep else keep[i % len(keep)], t, g, y) for i, (s, t, g, y) in enumerate(script)]
        name = f"{name[:-3] if name.endswith('_ja') else name}_{speakers}p"
    if engine == "espeak" and {s for s, *_ in script} - set(ESPEAK_VOICES):
        raise SystemExit("espeak は 2 人の会話だけ")
    for spk, text, gap, say in script:
        x = tts(spk, say, lang)
        x = 0.8 * x / (np.max(np.abs(x)) + 1e-9)
        t0 = max(0.0, cursor + gap)
        a = int(t0 * SR)
        need = a + len(x)
        if need > len(track):
            track = np.concatenate([track, np.zeros(need - len(track), dtype=np.float32)])
        track[a:need] += x
        t1 = t0 + len(x) / SR
        ln = {"speaker": spk, "text": text, "t0": round(t0, 3), "t1": round(t1, 3)}
        if say != text:
            ln["say"] = say  # TTS に実際に読ませた文（text は本来の文。参照 run の G2P は text から作る）
        lines.append(ln)
        cursor = max(cursor, t1)
    track = np.concatenate([track, np.zeros(int(0.8 * SR), dtype=np.float32)])
    track = np.clip(track, -1, 1)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{name}_{engine}"
    sf.write(out / f"{stem}.wav", track, SR, subtype="PCM_16")
    vs = (KOKORO_VOICES_EN if lang == "a" else KOKORO_VOICES) if engine == "kokoro" else (ESPEAK_VOICES_EN if lang == "a" else ESPEAK_VOICES)
    meta = {"name": stem, "engine": engine, "lang": "en" if lang == "a" else "ja",
            "voices": {k: v for k, v in vs.items() if k in {s for s, *_ in script}},
            "note": "t0/t1 は合成時に台詞を置いた時刻（台詞の前後の無音も含む）", "lines": lines}
    (out / f"{stem}.script.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out / f"{stem}.wav", f"{len(track) / SR:.1f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["kokoro", "espeak"], default="kokoro")
    ap.add_argument("--name", default="conv2_ja", choices=sorted(SCRIPTS))
    ap.add_argument("--out", default="samples")
    ap.add_argument("--speakers", type=int, help="conv5_ja を 3〜5 人に絞る")
    a = ap.parse_args()
    build(a.name, a.engine, Path(a.out), a.speakers)
