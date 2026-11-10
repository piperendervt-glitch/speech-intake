# speech-intake

マイクまたは録音から、**発話レコード**（いつ・誰が・何を言ったか）の流れを出す入力モジュール。
face-track-map / why-probe / Lumi に共通する入力側の部品。英語の発音を音素列として観察することもできる（段6〜9、下の「英語の発音を音素で見る」）。

- 推察（解釈・要約・文脈判定）は書かない。出力は観察表（発話レコードの列）だけ。
- 推察は利用側が別ファイル（推察表）に書き、各行に `refs: ["utt_000123", ...]` を必ず付ける。型は `intake/schema`。
- 待ち受けは localhost のみ。外部への送信はしない。API キー不要。

## 使い方

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows（mac/Linux は source .venv/bin/activate）
pip install -e .[dev,vad,stt,speaker]
# NVIDIA GPU で STT を動かすとき（Windows）。DLL の場所は intake が自動で通す
pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12>=9,<10"
python -m intake models speaker          # 話者分離モデル（GitHub, 約 40 MB, トークン不要）
# whisper のモデルは初回の実行で HuggingFace から自動取得（トークン不要）。2 回目以降はネットに問い合わせない

# 確認用サンプルを Kokoro で作り直すときだけ
pip install -e .[samples] && python -m unidic download   # 日本語辞書（unidic）が別に要る
python tools/make_samples.py --engine kokoro

python -m intake devices                 # 入力機器の一覧（ホスト API・既定のレート・開けるか・Bluetooth らしいか）
python -m intake devcheck --device 名前   # 入力機器の診断（帯域・頭の欠け・雑音・途切れ。段11）
python -m intake record                  # マイクから録音（Ctrl+C で止める）→ sessions/<日時>/audio.wav
python -m intake import 会話.m4a --replay  # 手元の音声ファイルをセッションにして流し直す
python -m intake replay <session>        # 保存した録音を流し直す（--speed realtime で実時間）
python -m intake replay <session> -c config/other.yaml --run other   # 別の設定で流して比較用の結果を作る
python -m intake view                    # 閲覧画面 http://127.0.0.1:8765/
python -m intake netcheck                # 待ち受けが localhost だけか確かめる
```

設定は YAML 1 つ。用意してある設定：`config/default.yaml`（何もしない）、`silero.yaml`（VAD のみ）、
`whisper.yaml`（VAD+STT）、`full.yaml`（VAD+STT+話者分離）、`stream.yaml`（full+WebSocket 配信）、
`live.yaml`（ライブ会話向け、段10）・`live_phones.yaml`（live＋音素）。各段の `provider` 名で実装を差し替える。
手元専用の設定は `config/local*.yaml` に置くと git に入らない。

## 置き場所

```
sessions/<session>/audio.wav                  16 kHz mono。時刻の唯一の基準（git に入らない）
sessions/<session>/runs/<run>/utterances.jsonl 観察表（発話レコード）
sessions/<session>/runs/<run>/trace/*.jsonl   段ごとの記録（VAD 区間など。閲覧画面での確認用）
```

時刻はすべて「セッション開始からの秒」。

## 合成サンプルで確かめる

```bash
python -m intake import samples/conv2_ja_kokoro.wav --label kokoro          # → セッション id
python tools/script_to_run.py <session> samples/conv2_ja_kokoro.script.json  # 台本どおりの正解を run「script」に
python -m intake -c config/full.yaml replay <session>
python -m intake view    # 設定 A=script、設定 B=full で上下に比較
```

## 公開データで確かめる（Common Voice 日本語、CC0）

Mozilla Data Collective（https://datacollective.mozillafoundation.org/）で「Common Voice Scripted Speech - Japanese」を
ログインしてダウンロードし、`data/`（git に入らない）に展開する。別々の人の読み上げをつないで、正解つきの疑似会話を作る：

```bash
python tools/make_cv_conversation.py data/cv-corpus-…/ja --speakers 3 --turns 16 --overlap   # → data/cv_conv/cv_conv_1.wav
python -m intake import data/cv_conv/cv_conv_1.wav --label cv
python tools/script_to_run.py <session> data/cv_conv/cv_conv_1.script.json
python -m intake -c config/full.yaml replay <session>
```

## 英語の発音を音素で見る（段6〜9）

Whisper は誤った発音を正しい英単語に直して書くので、文字起こしとは別に、音素認識器が聞き取った音素列（IPA）を発話レコードの
`phones` に入れる（schema 0.2）。期待される音素との差は**並べて見せるだけ**で、誤りの判定はしない（利用側が推察表に書く）。

```bash
pip install -e .[phones,g2p,samples-en]                 # torch は上の GPU 版を使う。allosaurus は任意（pip install allosaurus）
python -m intake import samples/en_misread_kokoro.wav --label en-misread    # rice→lice などわざと誤読させた合成音声
python -m intake -c config/phones.yaml replay <session>                      # 初回に wav2vec2（約 1.2 GB、トークン不要）を取得
python tools/ref_to_run.py <session> samples/en_misread_kokoro.script.json   # 台本の G2P を参照 run「ref_g2p」に → 自動で整列
python -m intake view    # 「音素」をオン。認識した音素・参照の音素・差分の色（一致／置換／脱落／挿入）、音素クリックでその前後を再生

python tools/import_saa.py --japanese all --native same --replay   # Speech Accent Archive（OSF、ログイン不要、data/ に置く）
python tools/import_idea.py --pages 1-17 --replay                  # IDEA の日本の話者（個人の研究目的のみ）
python tools/align_report.py --run phones --ref ref --corpus saa   # 群（japanese / native）ごとの数
```

粗い音素クラスの表は `intake/phones/classes.yaml`（41 クラス、直してよい）。利用側への申し送りは `docs/INTEGRATION_JA.md` §4。

## ライブ会話向けの通知（段10）

発話が始まった時点で `speech_start`、区間が閉じた時点で `speech_end` を STT を待たずに WebSocket で配る（turn-bench 向け）。
判断（割り込む・相槌など）はしない。ヘッドホン前提。

```bash
python -m intake -c config/live.yaml record                              # VAD + large-v3-turbo、話者分離・音素なし
python tools/subscribe.py --events                                       # speech_start / speech_end / utterance を時刻つきで
python -m intake -c config/live.yaml replay <session> --speed realtime   # 同じ通知を録音の流し直しで再現
python tools/live_latency.py <session> --run live                        # 区間ごとの遅れ
python -m intake view    # 「通知」「遅れを帯で」をオン
```

メッセージの順序と `seg` での対応付けは `docs/INTEGRATION_JA.md` §5。

### turn-bench からの申し送り（段11）

```bash
python -m intake -c config/live.yaml record --input ja1          # AirPods のマイク（subvocal-robot の airpods.bat が送る JA1 行、127.0.0.1:9750）
python tools/send_ja1.py samples/conv2_ja_kokoro.wav             # AirPods が無いときの試験用の送り手
python -m intake -c config/live_partial.yaml record              # 暫定の文字（partial）も配る
python tools/subscribe.py --events --min-silence-ms 200          # 繋いだときに VAD の min_silence_ms を変える
```

`hello` の `info` に `sessions_dir` / `session_dir`（絶対パス）が入る。詳しくは `docs/INTEGRATION_JA.md` §5.5、報告は `docs/REPORT_段11.md`。

## Bluetooth ヘッドセットを使う（段11）

AirPods などの Bluetooth ヘッドセットを、Windows 標準の Bluetooth（アダプタ）で PC につなぎ、そのマイクを入力にする。
speech-intake から見れば普通の入力機器（subvocal-robot の airpods.bat を通す `record --input ja1` とは別の口。上の節）。

**1. ペアリング**：Windows の「設定 → Bluetooth とデバイス → デバイスを追加 → Bluetooth」。AirPods はケースを開けて裏のボタンを
長押し（ライトが白く点滅）。ペアリングはこちらからは操作しない。

**2. Stereo と Hands-Free の違い**：Windows には同じヘッドセットが 2 つの顔で出る。

| 名前の例 | Bluetooth の接続 | 音 | マイク |
|---|---|---|---|
| ヘッドホン (AirPods **Stereo**) | A2DP | 高音質の出力だけ | 無い |
| ヘッドセット (AirPods **Hands-Free**) | HFP（通話用） | 出力も入力も電話の音質 | **ある**（16 kHz の広帯域か、8 kHz の狭帯域） |

マイクを開くと接続が HFP に切り替わり、その間は音楽の再生も電話の音質になる（数百 ms〜1 秒ほど音が途切れる）。
広帯域（mSBC）になるか狭帯域（CVSD）になるかは、アダプタ・ドライバ・Windows の版・ヘッドセットの組み合わせで決まり、
Windows の画面からは分からない。なので実際に録って中身で確かめる（下の `devcheck`）。

**3. 機器を選ぶ**：

```bash
python -m intake devices
```

同じ AirPods が API ごと（MME / Windows DirectSound / Windows WASAPI / Windows WDM-KS）に何行も出る。
`開` の列が ○（16 kHz で開ける）か △（既定のレートでなら開ける。取り込みで 16 kHz に直す）の行が使える。`BT` は名前から
Bluetooth らしい行。**MME の Hands-Free の行**から試すのがよい（名前が 31 文字で切れていることがある）。
番号は抜き差しで変わるので、設定には名前の一部を書く：`capture.device: "AirPods@MME"`（`@` の後ろはホスト API の名前の一部）。
複数当たると候補を出して止まる。

**4. 帯域を確かめる**：

```bash
python -m intake devcheck --device "AirPods@MME" --label airpods     # 8 秒録る。画面の英文を読む
python -m intake devcheck --device "Realtek@MME" --label pcmic       # 比べる相手（PC のマイク）
python -m intake view    # セッション airpods を開き、比較のセッションに pcmic を選ぶ。「スペクトログラム」をオン
```

読む英文（「s」「sh」「th」は 4 kHz より上に成分が出る）：

- She sells sea shells by the seashore.
- This fish shop sells six thick socks.
- Sixty-six thistles, three thin sheets.

判定は「広帯域らしい／狭帯域らしい／判断しにくい」の目安だけ。**決めるのはスペクトログラム**：4 kHz の点線より上に
「s」「sh」の縦の筋が見えれば広帯域、点線より上が真っ黒なら狭帯域。結果は `sessions/<session>/devcheck.json` にも残る
（頭の欠け＝マイクを開いた直後の無音の長さ、雑音の大きさ、途切れの位置）。

**5. 録る**：

```bash
python -m intake -c config/live.yaml --set capture.device="AirPods@MME" record   # speech_start → utterance が届く
```

PC のマイクで同じ発話を録っておくと、閲覧画面で上下に並べて、文字起こしと単語の `p` の違いを見られる（比較のセッション）。
録音中に機器が消えたら（電池切れ・接続切れ）、それまでを保存して止まる（`session.json` の `ended_by: device_lost`）。

**6. 入力の遅れを測る**（任意）：

```bash
python tools/latency_pair.py --a "Realtek@MME" --b "AirPods@MME"     # 15 秒、両方で同時に録る。2〜3 秒おきに手を叩く
```

「B は A より N ms 遅い」と出る。`capture.latency_ms: N` を設定に書くと、`speech_start` / `speech_end` に話した時刻の見積もり
`t_said_unix` が付く（`docs/INTEGRATION_JA.md` §5.6）。閲覧画面では A を開くと B が並び、手を叩いた所のずれが見える。

**狭帯域だったときに試すこと**：

1. Windows Update（Bluetooth のドライバや通話用の音声の扱いが更新されることがある）。
2. アダプタのドライバを、メーカー（Intel・Realtek など）の新しいものにする。汎用のドライバでは広帯域（mSBC）を使えないことがある。
3. 別のアダプタ（Bluetooth 5.x で広帯域音声に対応をうたうもの）。買い替えの判断は `devcheck` の結果で。
4. 別の行（ホスト API）を試す。ただし帯域は Bluetooth の接続で決まるので、行を変えても中身は同じことが多い。

## 段ごとの報告

`docs/REPORT_段n.md`。ダウンロードや長い処理の事前記録は `docs/LOG.md`。
