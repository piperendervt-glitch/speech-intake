# 利用側への申し送り（speech-intake v0.1）

speech-intake が出すのは**観察表**（発話レコードの列）だけ。解釈・要約・文脈判定などの推察は、各プロジェクトが
**推察表**として別ファイルに書く。推察表の各行は、根拠にした発話レコードの id を `refs` で**必ず**参照する。

- 発話レコードの型：`intake/schema/records.py`（`Utterance`, `Word`）、JSON Schema：`docs/schema/utterance.schema.json`
- 推察レコードの型：`intake/schema/records.py`（`Inference`）、JSON Schema：`docs/schema/inference.schema.json`
- `intake/schema` は標準ライブラリだけに依存する。利用側はこのフォルダだけをコピーしても使える。
- 時刻はすべて「セッション開始からの秒」（録音の音声サンプルが唯一の時計）。

発話レコードの欄：

| 欄 | 意味 |
|---|---|
| id | `utt_000123`。セッション・run の中で一意。推察表の `refs` はこれを指す |
| session | `20261109_160000` 形式のセッション id |
| t0, t1 | 発話の始まりと終わり（秒） |
| text, lang, words | 文字起こし。words は単語ごとの `{w, t0, t1, p}`（p は STT が出した確率をそのまま転記） |
| speaker, speaker_conf, speaker_source | 話者ラベル。source は `acoustic`（声で分離）/ `wearer`（装着者区間との重なり）/ `none`。conf は「その話者が発話の時間に占める割合」 |
| vad, stt | 使った VAD・STT の名前（例 `silero-v6`, `faster-whisper-large-v3`） |

---

## 1. face-track-map（最初の利用者）

### 発話レコードを購読する

1. speech-intake 側で配信を有効にして動かす（localhost のみで待ち受ける）。
   ```bash
   python -m intake -c config/stream.yaml record                 # ライブ
   python -m intake -c config/stream.yaml replay <session> --speed realtime --hold   # 録音を流し直す
   ```
   `config/stream.yaml` は `sink.websocket.enabled: true`（`ws://127.0.0.1:8766/ws`）。
2. face-track-map 側で WebSocket に接続する。最小のコードは `tools/subscribe.py`（aiohttp のみ）。
3. 届くメッセージ（JSON）：

   | type | 中身 | 受け取ったら |
   |---|---|---|
   | `hello` | `info`（session, run, providers, 設定） | 手元の発話レコードを空にする（新しい run の始まり） |
   | `utterance` | `record`（発話レコード 1 つ） | id をキーに保存 |
   | `revise` | `records`（その run の全発話レコード） | **全部置き換える**。話者分離（acoustic）は録音を止めた時点でまとめて話者を付けるので、ライブ中の `speaker` は空で、最後に revise が届く |
   | `trace` | `stage`（`vad` / `speaker`）, `row` | `?trace=1` で接続したときだけ届く。確認用 |
   | `end` | `stats` | 入力の終わり |

   途中から接続しても、その run の `hello` とそれまでの発話レコード（または最新の `revise`）が先に届く。

### 推察表の書き方

- 発話レコードとは**別のファイル**に書く（例 `face-track-map/inferences/<session>.jsonl`）。speech-intake は推察表を読みも書きもしない。
- 1 行 = 1 つの推察。`refs` は 1 つ以上必須。
  ```json
  {"id": "inf_0001", "refs": ["utt_000123", "utt_000124"], "kind": "addressee", "value": "face_2",
   "by": "face-track-map/gaze-v1", "conf": 0.7, "note": "発話中に face_2 の方を向いていた"}
  ```
- Python なら型と検証をそのまま使える：
  ```python
  from intake.schema import Inference
  row = Inference(id="inf_0001", refs=["utt_000123"], kind="addressee", value="face_2", by="face-track-map/gaze-v1").to_dict()
  ```
  `to_dict()` は `refs` が空・形式違いなら `SchemaError` を出す。
- 発話レコードは run（設定）ごとに id が振り直される。推察表には、どの session・run の発話を見たかを別に残しておく（例：ファイル名や先頭行に `session` と `run`）。
- 顔による話者ラベルは将来 `speaker_source: "face"` として入る予定。face-track-map が推した話者は、それまでは推察表に書く。

---

## 2. subvox（顎モーション検出器）

### `speaker.wearer` が受け取る区間データ

`speaker.provider: wearer` にすると、speech-intake は装着者の判定をせず、外部から届いた「装着者が話していた区間」と
発話レコードの重なりで `speaker` を付ける（`wearer` / `not_wearer`、`speaker_source: "wearer"`）。

- 区間の形式：`{"t0": 12.30, "t1": 13.90}`（セッション開始からの秒。`by`, `conf` は任意で、記録に残すだけ）
- 渡し方は 2 通り：
  1. **ファイル**：`sessions/<session>/wearer.jsonl` に 1 行ずつ追記する（行の終わりに改行。書きかけの行は次回読む）。
  2. **WebSocket**：`ws://127.0.0.1:8766/ws` に `{"type": "wearer", "t0": 12.3, "t1": 13.9}` を送る。
- 設定例：
  ```yaml
  speaker:
    provider: wearer
    path: wearer.jsonl     # セッションのフォルダ基準
    min_ratio: 0.5         # 発話の時間の半分以上が装着者区間なら wearer
  ```
- 時刻の合わせ方：マイク録音のセッションは `session.json` に `t0_unix`（最初のサンプルの壁時計、概算）を書く。
  subvox の時刻（UNIX 秒）から `t0_unix` を引けばセッション秒になる。OS のバッファ分（数十 ms）ずれうるので、
  厳密に合わせたいときは録音の頭で手を叩くなど、両方に残る合図で補正する。
- 録音を止めた時点（finalize）で、後から届いた区間も含めて付け直し、`revise` を出す。

### 一人で話した収録で「VAD ON ＝ 装着者」を先生にする

1. 装着者一人だけが話す状態で録音する：`python -m intake record`（subvox も同時に記録しておく）
2. VAD を流す：`python -m intake -c config/silero.yaml replay <session>`
3. 閲覧画面で VAD 区間が発話に合っているか目で確かめる（`python -m intake view`）。合っていなければ `--set vad.threshold=...` などで流し直す。
4. 教師データとして取り出す：
   ```bash
   python tools/export_vad_teacher.py <session> --run silero --out teacher.jsonl
   ```
   1 行 = `{"t0", "t1", "label": "speech", "vad", "p", "t0_unix", "t1_unix"}`。区間の外は「話していない」。
   `t0_unix` は subvox の記録と突き合わせるための壁時計（概算）。

注意：他人の声やテレビの音が入ると、それも speech になる（VAD は誰の声かを区別しない）。

---

## 3. Lumi Bench：4 段をノードとして独立に呼ぶ

各段はそれぞれ単独で呼べる。入出力は numpy 配列と `intake.schema` の dataclass だけ。

```python
import numpy as np
from intake.audio import AudioChunk
from intake import registry
from intake.config import load_config

cfg = load_config("config/whisper.yaml")
vad = registry.create("vad", cfg)          # VadProvider
stt = registry.create("stt", cfg)          # SttProvider
spk = registry.create("speaker", cfg)      # SpeakerProvider
```

| 段 | 呼び方 | 入力 | 出力 |
|---|---|---|---|
| VAD | `vad.feed(AudioChunk(t0, samples))` を順に、最後に `vad.flush()` | 16 kHz mono float32 | 閉じた区間 `list[VadSegment(t0, t1, vad, p)]` |
| STT | `stt.transcribe(audio, t0, 16000)` | 区間の音声と、その先頭のセッション秒 | `list[SttResult(t0, t1, text, lang, words)]`（時刻はセッション秒） |
| 話者 | `spk.feed(chunk)`（全音声）→ `spk.finalize(utts)`。acoustic は `spk.diarize(audio, 16000)` で区間だけも取れる | 音声と発話レコード | 話者を付けた `list[Utterance]` |
| 出力 | `JsonlSink(run_dir)` / `WebSocketSink()` の `start(info) → emit(utt) → revise(utts) → end(stats) → close()` | 発話レコード | ファイル / 配信 |

- 全段をつないだものが `intake.pipeline.Pipeline`（`feed(chunk)` → `finish()`）。
- provider は設定の `provider` 名で差し替える（`intake/registry.py`）。新しい実装は同じメソッドを持つクラスを書き、表に 1 行足す。
- 段ごとの確認用の記録（VAD 区間・枠ごとの確率・話者分離の区間）は provider の `drain_trace()` から `(stage, row)` で取り出せる。
