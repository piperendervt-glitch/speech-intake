# REPORT 段10：ライブ会話向けの即時通知（turn-bench 向け）

依頼文：`docs/03_REQUEST_live_events_JA.md`（v0.3）

## 到達点

```bash
python -m intake -c config/live.yaml record                               # マイク
python tools/subscribe.py --events                                        # 購読側
python -m intake -c config/live.yaml replay <session> --speed realtime    # 録音の流し直しで同じ通知
```

話し始めると `speech_start`、黙ると `speech_end`、少し遅れて `utterance` が届く。ユーザーの PC（RTX 5060 Ti）で
replay（実時間）の購読側の出力（`conv2_ja_kokoro.wav`、live.yaml）：

```
12:10:04.941 (+   0ms) speech_start   seg=0    t=    0.96  確定     1.02  p=0.90
12:10:10.093 (+  -0ms) speech_end     seg=0        0.86-    5.96  確定     6.18
12:10:10.610 (+   1ms) utterance      seg=0     STT 516ms  utt_000000     0.86-    5.68   おはようございます。先月の売上げの件で少し相談してもいいですか。
12:10:11.373 (+  -0ms) speech_start   seg=1    t=    7.39  確定     7.46  p=0.81
12:10:15.025 (+   0ms) speech_end     seg=1        7.29-   10.88  確定    11.10
12:10:15.214 (+  -0ms) utterance      seg=1     STT 190ms  utt_000001     7.29-   10.67   はいどうぞなんか気になるところがありましたか
```

（+ms は speech-intake が出してから購読側が受け取るまで。同じ PC で 0〜1 ms。）

## 確定事項

- **WebSocket の新しいメッセージ**（追加だけ。既存の `hello` / `utterance` / `revise` / `trace` / `end` の意味は変えていない）：
  - `speech_start {seg, t, t_detect, t_unix, p}`：VAD が始まりを確定した時点。STT を待たない
  - `speech_end {seg, t0, t1, t_detect, t_unix}`：VAD が区間を閉じた時点。STT を待たない
  - `speech_dropped {seg, reason, t_unix}`：区間から発話レコードが出ないと決まったとき。`reason` は `vad_short`（min_speech 未満で VAD が捨てた）か `stt_empty`（STT が空）
  - `utterance` のメッセージ側（`record` の外）に `seg` と `timing {vad_close_unix, stt_done_unix}`（音素ありなら `phones_done_unix` も）
  - 依頼文に無い `t_detect` を足した（確定した枠の終わりのセッション秒。始まりの確定に要った時間が分かる）
- 1 区間の順序は必ず `speech_start → speech_end → utterance（1 つ以上）か speech_dropped`。`seg` は発話レコードの欄にせず、`trace/vad.jsonl` の区間に残す。
- 過去の `speech_*` は途中から接続した購読者に再送しない。
- **VAD**：`SileroVad.drain_events()` を足した。始まりの確定条件は `vad.start_frames`（threshold 以上の枠がいくつ続いたら始まりか。1 枠 32 ms）。既定 1 は段2〜9 と同じ挙動（区間も同じ）。live.yaml は 2。届かずに途切れた候補は `trace/vad_cand.jsonl` に残す。
- **pipeline**：`pipeline.stt_thread: true` で STT 以降を別スレッドで回す（live.yaml）。STT の最中に次の発話が始まっても `speech_start` は待たされない。その代わり、次の区間の `speech_start` が前の区間の `utterance` より先に届くことがある（INTEGRATION §5.2 に書いた）。
- 通知と発話の時刻は `runs/<run>/trace/events.jsonl` に残す。実時間の run（mic / replay:realtime）では、通知を出した壁時計を音声の時計（セッション秒）に直した `t_emit` も入れる（チャンクが届いた時刻 − チャンクの終わり、の最小をセッション 0 秒とみなす）。
- **replay の realtime** は、チャンクの終わりの時刻になってから渡すように変えた（マイクと同じ。段1〜9 は先頭の時刻に渡していて chunk_ms だけ早かった）。
- **設定**：`config/live.yaml`（Silero + large-v3-turbo、`language: ja`、`beam_size: 1`、`one_per_vad: true`、話者分離・音素なし、WebSocket、`capture.latency: low`）、`config/live_phones.yaml`（live ＋ wav2vec2_espeak、`language: en`）。
- `capture.latency`（PortAudio に頼む入力の遅れ）を足し、録音の `session.json` に `input_latency_s` と `block_ms` を書く。
- **閲覧画面**：「通知」をオンにすると、`speech_start`（緑▶）・`speech_end`（橙◀）・`utterance`（青●）・捨てた（灰）の印を、通知を出した時刻に出す（波形の段に細い縦線も）。「遅れを帯で」で区間ごとに 始まりの確定／終わりの確定／speech_end→utterance の帯（ms つき）。区間ごとの表もある。合否の線は引いていない。
- `tools/subscribe.py --events`（通知を時刻つきで。配信が始まる前から起動して待てる）、`tools/live_latency.py`（区間ごとの遅れの内訳。下の数字はこれで出した）。
- 利用側への申し送りは `docs/INTEGRATION_JA.md` §5。

## 実測した遅れの内訳（ユーザーの PC、replay --speed realtime）

列の意味（ms）：
- **始まりの確定**：threshold を越えた最初の枠の先頭 `t` → 確定 `t_detect`（音声の時計。`start_frames` × 32 ms）
- **始まりの配信**：確定 → `speech_start` を出した時刻（壁時計。ブロックの待ち＋処理）
- **終わりの確定**：区間の終わり `t1`（余白 100 ms 込み）→ `speech_end`。黙り始めからは ＋100 ms
- **STT**：`speech_end` → STT が終わるまで

| 実行（9 区間、日本語 43.7 秒。英語は 6 区間 18.6 秒） | 始まりの確定 | 始まりの配信 中央/最大 | 終わりの確定 | STT 中央/最大 |
|---|---|---|---|---|
| live（turbo, ja, ブロック 32 ms） | 64 | 10 / 14 | 220 | 198 / 516 |
| stt.model=small | 64 | 7 / 14 | 220 | 165 / 454 |
| stt.language=null（自動判定） | 64 | 2 / 7 | 220 | 355 / 754 |
| ブロック 16 ms | 64 | 12 / 16 | 220 | 214 / 640 |
| ブロック 100 ms | 64 | 65 / 93 | 220 | 216 / 514 |
| 英語（stt.language=en） | 64 | 7 / 15 | 220 | 205 / 412 |
| live_phones（英語、音素つき） | 64 | 4 / 7 | 220 | 196 / 669（utterance まで 214 / 924） |
| live ＋ --hold（途中で netcheck） | 64 | 9 / 14 | 220 | 223 / 516 |

見えたこと（判断はしない）：
- 話し始めから `speech_start` までは、およそ 64 ＋ 10 ms。Silero の確率が threshold を越える前の立ち上がりは含まない。
- 黙り始めから `speech_end` まで約 320 ms（min_silence 300 ＋ 枠の待ち）、そこから `utterance` まで turbo で中央 約 200 ms。最大はどの実行も最初の区間（GPU の温まり）。
- **ブロック長**：32 → 16 ms は始まりの配信がほぼ変わらない（VAD の枠が 32 ms なので）。100 ms にすると中央で 55 ms ほど遅れる。既定の 32 のままにした。
- small は turbo より中央で 30 ms ほど速いだけ。自動判定は STT が 1.8 倍ほど遅い。
- 音素（wav2vec2）を足すと utterance が中央で 20 ms、最初の区間は 400 ms ほど遅れる。`speech_start` / `speech_end` は変わらない。
- 同じ録音をどの設定・ブロック長で流しても、区間・通知の時刻（音声の時計）と発話の数は同じだった（日本語 9 区間 9 件、英語 6 区間 6 件）。テストでもチャンク 20 / 32 / 100 ms、fast / realtime で通知が一致することを確かめている。
- GPU のメモリ（turbo）：約 3.2 GB。

**マイク**：15 秒の録音を 2 回（誰も話していない）。block_ms 32：PortAudio の入力の遅れ 32 ms、overflows 0。block_ms 16：16 ms、overflows 0。
人が話したときの遅れは未測定（下の未解決点）。

## PC での確認で見つかって直したもの

- **`record` が必ず `FileExistsError` で止まっていた**：run の出力（`runs/<run>/`）を作った後で、マイク側がセッションを作ろうとしていた。先にセッションを作るようにし、CLI のテストを足した（PC の stash@{0} にあった未コミットの直しと同じ所。stash には触っていない）。
- `record --seconds` がモデルの読み込みの時間も数えていた（15 秒で 11.7 秒しか録れない）：録ったサンプル数で止めるようにした。
- `tools/subscribe.py` を配信より先に起動すると接続できずに終わっていた：1 秒ごとに繋ぎ直すようにした。
- 区間が 1 つも無い run で `tools/live_latency.py` が落ちていた：そう表示して終わるようにした。

## 確認

- テスト 42 件（クラウド、PC）。段10 の分：通知の順序（start → end → utterance／dropped）、`seg` の対応、`start_frames`、短い区間の扱い、replay での再現（チャンク長・fast/realtime）、既存メッセージの互換（`record` の形が同じ、`seg`/`timing` 無しでも従来どおり）、`stt_thread` の有無、record の CLI。
- PC の pytest はそのままだと一時フォルダ（AppData\Local\Temp\pytest-of-…）に書けず 10 件がエラーになる。`--basetemp` を別の場所にすると全件成功。実行環境の権限の問題で、コードの問題ではない。
- `python -m intake netcheck`：クラウドと PC の両方で、配信中に「すべて localhost のみ」（127.0.0.1:8766）。

## 未解決点

- **人が話したマイク録音での遅れ**：上の数字は録音の流し直し。マイクでは、音が届いてからチャンクとして受け取るまでの入力の遅れ（PortAudio の見積もり 32 ms）が上の数字に足される。`t_emit` はチャンクが届いた時刻から音声の時計を決めるので、この分は `start_wall` に現れない。実際に話して確かめてはいない。`python -m intake -c config/live.yaml record` と `python tools/subscribe.py --events` を並べて話し、`python tools/live_latency.py <session> --run live` で見られる。
- `start_frames` の既定（live.yaml の 2）は合成音声で決めた。実際の部屋の雑音・咳・相槌で `speech_start` が出すぎないかは、閲覧画面（`trace/vad_cand.jsonl` の候補と通知の印）で見て調整する必要がある。
- 最初の区間だけ STT が 2〜3 倍遅い（温まり）。起動時に空の音声で一度 STT を回す手はあるが、入れていない。
- エコーキャンセルはしていない（ヘッドホン前提）。

## turn-bench への申し送り

- 割り込みには `speech_start` を使う。話し始めから約 75 ms（マイクなら入力の遅れ 約 32 ms が加わる）で届く（`start_frames: 2` のとき。1 にすると 32 ms 早まるが雑音で出やすい）。
- `speech_start` の後に `speech_dropped`（`vad_short` / `stt_empty`）が来ることがある。咳・物音・短い相槌で AI を黙らせた後、それが発話でなかったと分かるのはこの時点。どう扱うか（再開するか）は turn-bench の判断。
- 返答を始める合図に `speech_end` を使うと、黙ってから約 320 ms。文字が要るなら `utterance` を待って ＋約 200 ms。`min_silence_ms` を短くすれば早まるが、1 文が細切れになる。
- `seg` で突き合わせる。`utterance` の `id` とは別の番号。次の区間の `speech_start` が前の区間の `utterance` より先に来ることがある。
- 試験は録音の流し直し（`replay --speed realtime`）で、同じ通知が同じ間隔で再現する。
- 時刻の換算：`t_unix` は通知を出した壁時計、`t` / `t0` / `t1` はセッション秒。
