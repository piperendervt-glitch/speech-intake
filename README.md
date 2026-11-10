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

python -m intake devices                 # 入力デバイス一覧
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

## 段ごとの報告

`docs/REPORT_段n.md`。ダウンロードや長い処理の事前記録は `docs/LOG.md`。
