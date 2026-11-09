# REPORT 段1：schema + capture + replay + JSONL 出力 + 閲覧画面の骨組み

## 到達点

録音して WAV が保存され、流し直すと閲覧画面に波形が出る。

- 確認したこと（クラウド環境。マイクは無いので取り込みは疑似ストリームで確認）
  - `python -m intake import <wav> --replay` → `sessions/<id>/audio.wav` と `runs/default/{run.json,utterances.jsonl}` ができる
  - `python -m intake view` → セッション一覧から開くと波形が出る（ズーム・ドラッグ移動・クリック再生）
  - `MicCapture` に sounddevice の代わりの疑似ストリームを差し込み、WAV が欠けなく保存され、時刻がサンプル数だけで決まることをテストで確認
  - `python -m intake netcheck`：閲覧画面の待ち受けは `127.0.0.1:8765` のみ（OK）
- **ユーザー側で確かめてほしいこと**：お手元の PC で `python -m intake record` → `python -m intake view`。録音中も 2 秒ごとに波形が伸びる（自動更新）。

## 確定事項

- 時刻はすべて「セッション開始からの秒」。基準は**書いた音声サンプル数 / 16000**（壁時計は使わない）。
- 入口は 2 つ：`capture`（マイク、`intake/capture`）と `replay`（WAV、`intake/replay`）。どちらも `AudioChunk(t0, samples)` の列を出し、ほかの段はこれしか受け取らない。sounddevice を import するのは `intake/capture` だけ。
- `intake/schema` は標準ライブラリだけに依存（テストで import を検査）。中身：
  - `Utterance` / `Word`（観察表の行）、`Inference`（推察表の行。`refs` 1 つ以上必須）、`VadSegment`（段ごとの記録）
  - 検証関数 `validate_utterance` / `validate_inference`、JSON Schema（`docs/schema/*.json`。`python -m intake.schema` で再生成）
- 置き場所：`sessions/<session>/audio.wav`、`runs/<run>/utterances.jsonl`（観察表）、`runs/<run>/trace/<段>.jsonl`（VAD 区間などの記録。観察表ではない）、`runs/<run>/run.json`（使った設定のスナップショット）。同じ録音を別 run 名で流せば比較表示の A/B になる。
- 設定は YAML 1 つ。`vad/stt/speaker.provider` で差し替え（`intake/registry.py`）。重い依存は選ばれたときだけ import。
- 待ち受け host は設定読み込み時に検査し、`127.0.0.1 / localhost / ::1` 以外は起動を拒否。閲覧画面は CDN を含め外部を一切読み込まない。
- 段をつなぐ `Pipeline` は、段2以降の Provider 境界（`vad.feed / stt.transcribe / speaker.assign / sink.emit`）を先に決めた。各段は `none` 実装で通る。

## 選んだライブラリと理由

| 用途 | 採用 | 理由 |
|---|---|---|
| マイク | sounddevice | 依頼文の既定。Windows 用 wheel に PortAudio 同梱で導入が楽 |
| WAV | soundfile | 書き込み途中に `flush()` でヘッダが更新され、録音中でも閲覧画面から読める（確認済み） |
| 閲覧画面のサーバ | aiohttp | 静的配信・Range 付き音声配信・WebSocket（段5）を 1 つで賄える |
| 閲覧画面 | 素の JS + canvas | 外部ライブラリ無し＝オフラインで動き、外部への読み込みが起きない |
| 波形 | サーバ側で 5 ms ごとの min/max を作り int16 バイナリで渡す | 1 時間の録音でも約 2.9 MB。WAV の大きさ・更新時刻が変わったら作り直す |

## 未解決点

- **samples/ の 2 話者合成会話がまだ無い**：クラウドの作業環境からは HuggingFace（Kokoro のモデル置き場）と download.pytorch.org に接続できなかった。ユーザーの PC で作る（段3の前に行う予定）。
- Windows で「録音中の WAV を別プロセス（閲覧画面）が読む」ことは未確認（Linux では確認済み）。読めない場合は録音側で波形の元データを別に書き出す方式に変える。
- マイクの実機確認（入力オーバーフロー、デバイス既定のサンプルレートが 16 kHz でない場合）は未確認。sounddevice/PortAudio は 16 kHz を要求し、WASAPI 共有モードで受け付けない機器では失敗しうる。その場合はデバイスのレートで受けてリサンプルする処理を足す。

## 次段への申し送り

- 段2は Silero VAD を `intake/vad/silero.py` に置き、`feed()` で閉じた区間を返す。区間は `trace/vad.jsonl` に書き、閲覧画面の VAD 表示はそれを読む（表示側は実装済み）。
- 閲覧画面は段3〜段4の表示（文字・単語時刻・話者レーン・比較）まで先に作ってある。データが来れば出る。
