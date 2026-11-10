# REPORT 段11：turn-bench からの申し送りへの対応

（Bluetooth ヘッドセットの入力と機器の診断は別の報告：`docs/REPORT_段11_BT.md`）

申し送りの元：turn-bench `docs/HANDOFF_to_speech_intake_JA.md`（ブランチ claude/project-thread-e3vgat）の 4 件。
既存のメッセージの意味は変えていない（足しただけ）。turn-bench の `intake_client.py` は知らない type を読み飛ばすので、そのまま繋がる。

## 到達点

| 要望 | 必要度 | 対応 |
|---|---|---|
| `hello` の `info` に `sessions_dir` の絶対パス | 低 | `info.sessions_dir`（絶対パス）と `info.session_dir`（このセッションのフォルダ）を足した。`run.json` にも入る |
| 部分的な文字起こし（話している途中の暫定の文字） | 中 | 新しいメッセージ `partial`。`pipeline.partial`（`silence_ms` / `every_ms` / `min_ms`）で出す。`config/live_partial.yaml` |
| `min_silence_ms` を購読側から変えられる口 | 低 | 購読側から `{"type": "set", "vad": {"min_silence_ms": 200}}`。**接続ごとではなく全体に効く**（下に理由）。変わったら全員に `config` |
| JA1 の UDP（airpods.bat、127.0.0.1:9750）を入力にする口 | 中 | `record --input ja1`（`capture.provider: ja1_udp`）。試験用の送り手 `tools/send_ja1.py` |

使い方とメッセージの形は `docs/INTEGRATION_JA.md` §5.5。

## 確定事項

- **暫定の文字は発話レコードではない**。`utterances.jsonl`・途中から繋いだ購読者への再送には入れない（観察表は確定したものだけ）。
  `trace/events.jsonl` には残し、閲覧画面の「通知」に紫の点で出る（`tools/live_latency.py` の `partial_lead` 列は、最初の暫定が確定の
  `utterance` よりどれだけ先に出たか）。
- 暫定は「区間の終わりの少し前」に欲しいという要望なので、既定の出し方は**黙り始めてから `silence_ms` で 1 回**（`reason: "silence"`）。
  live_partial.yaml は 150 ms。`min_silence_ms` 300 なので、`speech_end` より約 150 ms 前に STT を始める。話している間の定期（`every_ms`）も選べる。
- 暫定は STT のスレッドが空いているときだけ頼む。STT は 1 本なので、暫定の最中に区間が閉じると確定の `utterance` が待たされる。
  待たせる量を暫定 1 回分にとどめるための決まり。`pipeline.stt_thread: false` では出ない。
- **`min_silence_ms` を接続ごとにしなかった理由**：VAD は 1 つで、`seg` の区間・STT に渡す音声・`utterances.jsonl` は全購読者で同じもの。
  接続ごとに閉じ方を変えるには VAD の閉じ方を購読者の数だけ持ち、STT もその数だけ回すことになる。turn-bench は購読者が 1 つなので、
  全体の設定を動かしたまま変える口で足りると判断した。変えた値は `config` の通知で全員に知らせ、次の run では設定ファイルの値に戻る。
  ついでに `start_frames` も同じ口で変えられる。他の項目（threshold など）は受け付けない（`error` を返す）。
- JA1 の受け方は subvocal-robot の `windows/jawrec/protocol.py` / `hfp_mic.py` に合わせた（1 行 20 ms、16 kHz int16、base64）。
  抜けたパケットは送り手の時計 `t` の飛びで判断して無音で埋める（1 行の抜けから。`session.json` の `ja1.lost_packets` / `filled_s`）。
  セッション秒は「書いたサンプル数 / 16000」のままなので、replay・閲覧画面はマイクの録音と同じに扱える。
- JA1 の待ち受けも localhost のみ（`capture.udp.host` が localhost 以外なら起動しない）。`python -m intake netcheck` は UDP の受け口も出すようにした。

## 確認

- テスト `tests/test_stage11_handoff.py`（9 件）：hello の絶対パス、暫定（黙りで 1 回／話している間の定期／stt_thread なしでは出ない）、
  購読側からの set（反映・範囲外と他の項目の拒否・speaker 向けのメッセージはそのまま渡る）、JA1 の解読（JR1 行は読み飛ばす）、
  UDP の取り込み（別の送り手を捨てる・抜けを無音で埋める・audio.wav）、localhost 以外の拒否。全体 51 件が通る。
- クラウドで通しの確認：`record --input ja1`（Silero のみ、STT なし）に `tools/send_ja1.py samples/conv2_ja_kokoro.wav --drop 0.02` で送り、
  `tools/subscribe.py --events --min-silence-ms 200` で受けた。`config` の通知 → `speech_start` / `speech_end` が届き、session.json に
  抜けた数が残った。
- **PC では未確認**：本物の AirPods（airpods.bat）からの入力、faster-whisper での暫定の文字（中身・`stt_ms`・確定の `utterance` が待たされる量）。

## 未解決点

- 暫定の文字の実測（PC）。`python -m intake -c config/live_partial.yaml replay <session> --speed realtime` と
  `python tools/live_latency.py <session> --run live_partial` で `partial_lead` と `utt_after_end`（live との差）を見る。
- AirPods の HFP（電話の帯域）で Whisper の誤りがどれだけ増えるか、VAD の threshold が合うか。
- airpods.bat の送り先は 1 つなので、jawchat と speech-intake で同時に AirPods の音声を受けることはできない（ポートを分けてどちらかを選ぶ）。
- AI の声を AirPods に返す口は speech-intake の範囲外（subvocal-robot 側で別に進める）。

## turn-bench への申し送り

- `hello` の `info.session_dir` を見れば、turn-bench の設定 `intake.sessions_dir` は要らなくなる（古い speech-intake では無いので、無ければ従来どおり）。
- `partial` は判断の材料が早く来るだけで、確定ではない。`utterance` と文字が違うことがある。`seg` で突き合わせる。
- `wait_ms` を 300 ms より短くしたいときは、繋いだ直後に `{"type": "set", "vad": {"min_silence_ms": N}}` を送る。
  speech-intake 全体の値が変わる（閲覧画面・utterances.jsonl の区間の切れ方も変わる）。`config` の通知が返ってきたら効いている。
- AirPods で話すとき：`airpods.bat` → `python -m intake -c config/live.yaml record --input ja1`。
