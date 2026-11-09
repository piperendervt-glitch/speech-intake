# REPORT 段4：speaker.acoustic + 話者レーン + 比較表示

## 到達点

2 人以上の録音で話者が分かれ、2 つの設定を上下に並べられる。

```bash
python -m intake models speaker                                  # 初回だけ（GitHub, 約 40 MB）
python -m intake -c config/full.yaml replay <session>            # run「full」
python tools/script_to_run.py <session> samples/conv2_ja_kokoro.script.json   # 台本の正解を run「script」に
python -m intake view    # 設定 A=script、設定 B=full。話者レーンの色分けで見比べる
```

## 確認（ユーザーの PC）

- Kokoro の 2 話者会話（43.7 秒）：台本の A→spk_1、B→spk_2。話者分離の 9 区間すべてが台本と一致（取り違え 0）。speaker_conf 0.87〜1.0。
- eSpeak の 2 話者会話（100.4 秒）：A→spk_1、B→spk_3。余分な spk_2 が短く 2 回（0.34 秒、1.03 秒）出て、発話 1 件に付いた。それ以外は一致。
- 話者分離の処理時間は CPU で数秒（full と whisper の wall_s の差：kokoro 約 5 秒、espeak 約 11 秒）。

## 選んだライブラリと理由

依頼文の候補は pyannote と NVIDIA Nemotron 3 Diarization。

- **既定は sherpa-onnx（pyannote segmentation 3.0 の ONNX 版 + 3D-Speaker ERes2Net の話者埋め込み + クラスタリング）。**
  - ユーザーの判断（2026-11-09）：HuggingFace のアカウント・トークンを使わない方式にする。
  - sherpa-onnx は区間検出に pyannote と同じ segmentation-3.0 を使う。モデルは GitHub から取れて、トークンも torch も要らない。Windows でも pip だけで入った。
- pyannote.audio（speaker-diarization-community-1）も `speaker.provider: acoustic_pyannote` として残した。使うには HuggingFace で利用条件に同意し、トークンを用意する。
- Nemotron 3 Diarization は NeMo が要る。依存が重く Windows での導入が難しいので、先には採用しなかった。

## 確定事項

- 話者分離はセッション全体をまとめて行う（finalize）。ライブ中に出る発話レコードは `speaker` が空で、録音を止めた時点で付け直した全列を `revise` として出す（JSONL は書き直し、WebSocket には `revise` を送る）。
- 各発話には、時間が最も長く重なった話者を付ける。`speaker_conf` は「その話者が発話の時間に占める割合」（確率ではない）。
- ラベルは登場順に `spk_1, spk_2, …`。同じ人でも run が違えば番号は一致しないことがある。
- 話者分離そのものの区間は `trace/speaker.jsonl`。閲覧画面の話者レーンは、上の細い帯が話者分離の区間、下の帯が発話レコードに付いた話者。
- 既定では話者分離を CPU で動かす（GPU は STT に譲る）。`speaker.device: cuda` で GPU も使える。
- 人数が分かっているときは `speaker.num_speakers` を指定する。自動のときは `speaker.threshold`（小さいほど話者が増える）で調整する。

## 未解決点

- ライブ中の話者付け：今は録音を止めるまで話者が付かない。face-track-map がライブで話者を必要とするなら、数十秒ごとに分離し直して revise を出す方式を足す。
- 実際の会話（3 人以上、声の似た人、重なり）での確認はまだ。
- 取得元の github.com の releases/download が PC でもクラウドでも 504 を返した。GitHub API の asset URL に自動で切り替えるようにした（PC では同じ方法で取得できた）。
