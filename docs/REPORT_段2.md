# REPORT 段2：Silero VAD + 閲覧画面に VAD 区間

## 到達点

流し直すと発話区間が波形に重なる。

```bash
python -m intake -c config/silero.yaml replay <session>                       # run 名 "silero"
python -m intake -c config/silero.yaml --set vad.min_silence_ms=800 replay <session> --run silero_sil800
python -m intake view    # 設定 A=silero、設定 B=silero_sil800 で上下に比較
```

- 閲覧画面：波形の上に VAD 区間（緑の帯）、枠（32 ms）ごとの発話確率（緑の線）、閾値（点線）を重ねる。「VAD 区間」のチェックで表示／非表示。
  帯をクリックするとその区間だけ再生、帯にマウスを重ねると元の記録（t0, t1, 平均確率）が出る。
- 確認（クラウド環境、eSpeak NG で合成した 2 話者・約 100 秒の会話 `samples/conv2_ja_espeak.wav`）：
  `min_silence_ms=300` で 15 区間、`800` で 8 区間。台本の全台詞の位置が区間に入ることをテストで確認。100 秒の音声の処理は CPU で約 1.1 秒。
- 段2の時点では STT が無いので、VAD 区間 1 つにつき文字が空の発話レコードを 1 つ出す（`stt: "none"`）。

## 確定事項

- Silero VAD は **ONNX 版を onnxruntime で動かし、torch を使わない**。モデルファイル（silero-vad 6.2.3 の `silero_vad.onnx`, 2.3 MB, MIT）を `intake/vad/models/` に同梱した。
  - 理由：PyPI の `silero-vad` パッケージは torch を必須にしており、VAD だけのために torch（GPU 版で数 GB）を入れることになる。onnxruntime なら約 15 MB。
  - 発話レコードの `vad` 欄は `"silero-v6"`。
- 区間の決め方（設定 `vad.*` で調整、`--set` で一時的に上書き可）：

  | 設定 | 既定 | 意味 |
  |---|---|---|
  | threshold | 0.5 | この確率以上で発話開始 |
  | neg_threshold | threshold−0.15 | 発話中、これ未満が min_silence_ms 続いたら終了 |
  | min_silence_ms | 300 | 区間を閉じる無音の長さ。短いと文が細切れ、長いと話者交代を跨ぐ |
  | min_speech_ms | 250 | これより短い区間は捨てる |
  | speech_pad_ms | 100 | 前後の余白（STT が語頭・語尾を落とさないため） |
  | max_speech_s | 28 | 超えたら区間を切る（Whisper の 30 秒窓に収める） |

- 枠ごとの確率は `trace/vad_prob.jsonl`、区間は `trace/vad.jsonl`。どちらも観察表ではなく、閲覧画面で確かめるための段ごとの記録。
- 区間が閉じるのは「無音が min_silence_ms 続いた時点」なので、ライブでは区間の終わりから min_silence_ms 遅れて発話レコードが出る。
- 設定ファイルに加えて、コマンドの `--set key=value`（何度でも）で一部だけ上書きできるようにした。比較表示用の run を作るのが楽になる。

## 未解決点

- 閾値・無音長の既定値は eSpeak の機械音声でしか見ていない。**実際の会話（ユーザーの録音）で、相づち・言いよどみ・重なりをどう切るかは目で確かめてほしい**。
- `samples/conv2_ja_espeak.wav` は開発用（機械的な声）。依頼文の Kokoro による自然な声のサンプルは、ユーザーの PC で作る（クラウド環境から HuggingFace に届かないため）。

## 次段への申し送り

- 段3の STT は VAD 区間の音声（前後に余白込み）を `stt.transcribe(audio, t0, sr)` で受け取り、セッション基準の時刻で単語を返す。
- STT 用のモデル取得と GPU はユーザーの PC で行う。
