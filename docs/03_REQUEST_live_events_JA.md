# speech-intake 依頼文 v0.3：ライブ会話向けの即時通知（段10）

対象リポジトリ：speech-intake（段1〜9 完了の続き）
前提：`docs/REPORT_段1〜9.md`、`docs/INTEGRATION_JA.md`、`intake/sink/websocket.py`、`intake/vad/silero.py`、`intake/pipeline.py`

## 0. 位置づけ

- 次の利用者は **turn-bench**（音声で AI と自然に会話できるかを確かめる新しいアプリ。別リポジトリ）。
- turn-bench は「ユーザーが話し始めた瞬間に AI が黙る（割り込み）」を作る。そのためには、発話が**始まった時点**で通知が要る。今の配信は、VAD 区間が閉じて STT が終わってから `utterance` が届くので、割り込みには遅い。
- 足すのは「始まった／終わった」という観察の通知と、ライブ向けの軽い設定だけ。割り込むかどうか・相槌かどうかの**判断は turn-bench 側**（推察）。speech-intake は判断しない。

## 1. ゴール

speech-intake を `record`（または `replay --speed realtime`）で動かすと、購読側に「発話が始まった」が数十〜百数十 ms で届き、続いて「発話が終わった」、その後に従来どおり `utterance` が届く。閲覧画面で各通知がいつ出たかを確かめられる。

## 2. やらないこと

- 割り込み・相槌・ターン終了の判断（turn-bench の仕事）
- エコーキャンセル（スピーカー出力の回り込み対策）。当面はヘッドホン前提
- 既存メッセージ（`hello` / `utterance` / `revise` / `trace` / `end`）の意味の変更。追加だけにする

## 3. 追加するもの

### 3.1 WebSocket の新しいメッセージ
| type | 中身 | いつ出す |
|---|---|---|
| `speech_start` | `seg`（区間の通し番号）, `t`（セッション秒）, `t_unix`, `p` | VAD が発話の始まりを確定した時点。**STT を待たない** |
| `speech_end` | `seg`, `t0`, `t1`, `t_unix` | VAD が区間を閉じた時点。**STT を待たない** |
| `utterance`（既存） | 従来どおり。対応が取れるよう `seg` を**メッセージ側**（`record` の外）に足す | STT 完了時 |

- `seg` は発話レコードの欄にしない（配信の対応付け用。観察表の形は変えない）。JSONL の `trace/vad.jsonl` には残す
- 1 つの VAD 区間が複数の `utterance` に分かれる／空になる場合があることを INTEGRATION に明記する（空のときは `utterance` が出ない代わりに `speech_dropped`（`seg`）を出す）
- `utterance` のメッセージ側に、遅れの内訳を足す：`timing: {vad_close_unix, stt_done_unix}`（閲覧と turn-bench の時間軸表示用）
- 途中から接続した購読者への再送は従来どおり発話レコードだけ（過去の `speech_start` は再送しない）

### 3.2 VAD の「始まり」の出し方
- 今の SileroVad は区間が閉じたときに `VadSegment` を返す。これに加えて、始まりを確定した時点でイベントを取り出せる口（例：`drain_events()`）を足す
- 始まりの確定条件（何枠続いたら始まりとするか）は設定 `vad.start_frames`（既定は今の挙動から自然な値）。短い雑音で `speech_start` が出すぎないかを閲覧画面で見て調整できるようにする
- `replay` でも同じ通知が同じ順で出る（`--speed realtime` なら実時間の間隔で）。**turn-bench が録音の流し直しで試験できること**がこの段の要

### 3.3 ライブ向けの設定 `config/live.yaml`
- 返答の速さを優先した構成：VAD（`min_silence_ms` を設定で変えられる）＋ STT（`large-v3-turbo` か `small`、`language` は設定で `en` / `ja` / 自動）＋ 話者分離なし ＋ 音素なし ＋ WebSocket 配信
- マイクのブロック長（1 回に渡す音声の長さ）が遅れに効くので、`capture` の設定で小さくできるようにし、既定値と変えたときの違いを REPORT に書く
- 音素（`phones`）を足した `config/live_phones.yaml` も置く（保存だけしたい利用者向け。遅れがどれだけ増えるかを REPORT に書く）

### 3.4 閲覧画面
- 時間軸に `speech_start` / `speech_end` の印を足す（VAD 区間の帯に対して、通知が出た時刻）
- 区間ごとに「始まりの確定までの遅れ」「区間が閉じてから `utterance` が出るまでの遅れ」を帯の長さで見られる表示（切り替え）
- 数値の閾値による合否は決めない。見て判断する

### 3.5 購読の例と文書
- `tools/subscribe.py` に新しいメッセージの表示を足す（`--events` で `speech_start` / `speech_end` を時刻つきで出す）
- `docs/INTEGRATION_JA.md` に §5「turn-bench（ライブ会話）」を足す：メッセージの順序、`seg` での対応付け、`timing` の読み方、ヘッドホン前提であること

## 4. 到達点（段10）

`python -m intake -c config/live.yaml record` と `python tools/subscribe.py --events` を並べて動かし、話し始めるとすぐ `speech_start` が出て、黙ると `speech_end`、少し遅れて `utterance` が出る。同じことが `replay <session> --speed realtime` でも再現する。

## 5. 進め方の約束

- 任された範囲は確認を待たずに進め、`docs/REPORT_段10.md` で報告する（確定事項・実測した遅れの内訳・未解決点・turn-bench への申し送り）
- モデルのダウンロードや 1 時間を超えそうな処理は、見込みの時間と目的を先に `docs/LOG.md` に書いてから実行する
- 待ち受けは localhost のみ。`python -m intake netcheck` を実行する
- テストは軽く：イベントの順序（start → end → utterance）、`seg` の対応、replay での再現、既存メッセージの互換
