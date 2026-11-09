# speech-intake

マイクまたは録音から、**発話レコード**（いつ・誰が・何を言ったか）の流れを出す入力モジュール。
face-track-map / why-probe / Lumi に共通する入力側の部品。

- 推察（解釈・要約・文脈判定）は書かない。出力は観察表（発話レコードの列）だけ。
- 推察は利用側が別ファイル（推察表）に書き、各行に `refs: ["utt_000123", ...]` を必ず付ける。型は `intake/schema`。
- 待ち受けは localhost のみ。外部への送信はしない。API キー不要。

## 使い方

```bash
python -m venv .venv && .venv\Scripts\activate      # Windows（mac/Linux は source .venv/bin/activate）
pip install -e .[dev]

python -m intake devices                 # 入力デバイス一覧
python -m intake record                  # マイクから録音（Ctrl+C で止める）→ sessions/<日時>/audio.wav
python -m intake import 会話.m4a --replay  # 手元の音声ファイルをセッションにして流し直す
python -m intake replay <session>        # 保存した録音を流し直す（--speed realtime で実時間）
python -m intake replay <session> -c config/other.yaml --run other   # 別の設定で流して比較用の結果を作る
python -m intake view                    # 閲覧画面 http://127.0.0.1:8765/
python -m intake netcheck                # 待ち受けが localhost だけか確かめる
```

設定は YAML 1 つ（`config/default.yaml`）。各段の `provider` 名で実装を差し替える。
手元専用の設定は `config/local*.yaml` に置くと git に入らない。

## 置き場所

```
sessions/<session>/audio.wav                  16 kHz mono。時刻の唯一の基準（git に入らない）
sessions/<session>/runs/<run>/utterances.jsonl 観察表（発話レコード）
sessions/<session>/runs/<run>/trace/*.jsonl   段ごとの記録（VAD 区間など。閲覧画面での確認用）
```

時刻はすべて「セッション開始からの秒」。

## 段ごとの報告

`docs/REPORT_段n.md`。ダウンロードや長い処理の事前記録は `docs/LOG.md`。
