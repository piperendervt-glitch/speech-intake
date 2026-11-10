# 利用側への申し送り（speech-intake v0.2）

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
| phones, phones_model, phones_source | schema 0.2。どう発音されたかの音素列（§4）。欄が無い 0.1 のレコードは空として読む |

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
| 音素 | `phones.recognize(audio, t0, 16000)`（`registry.create("phones", cfg)`） | 同上 | `list[Phone(ph, t0, t1, p)]`（IPA、セッション秒） |
| 話者 | `spk.feed(chunk)`（全音声）→ `spk.finalize(utts)`。acoustic は `spk.diarize(audio, 16000)` で区間だけも取れる | 音声と発話レコード | 話者を付けた `list[Utterance]` |
| 出力 | `JsonlSink(run_dir)` / `WebSocketSink()` の `start(info) → emit(utt) → revise(utts) → end(stats) → close()` | 発話レコード | ファイル / 配信 |

- 全段をつないだものが `intake.pipeline.Pipeline`（`feed(chunk)` → `finish()`）。
- provider は設定の `provider` 名で差し替える（`intake/registry.py`）。新しい実装は同じメソッドを持つクラスを書き、表に 1 行足す。
- 段ごとの確認用の記録（VAD 区間・枠ごとの確率・話者分離の区間）は provider の `drain_trace()` から `(stage, row)` で取り出せる。

---

## 4. Lumi（英会話学習）：発音を音素で観察する

Whisper は誤った発音を正しい英単語に直して書く（合成音声の誤読 very→berry・think→sink・thank→sank を、正しい綴りで書いた。
Speech Accent Archive の日本語話者の朗読でも原文の 95% の語をそのまま書いた）。そこで speech-intake は、文字起こしとは別に
**音素認識器が聞き取った音素列**を発話レコードに入れる。「期待される音素と違う＝誤り」という判定は推察なので、
**Lumi が推察表（`refs` 必須）に書く**。speech-intake は判定しない。

### 4.1 発話レコードの `phones` の読み方

`config/phones.yaml`（VAD ＋ Whisper（`language: en`）＋ 音素認識）で流すと、各発話に次が入る。

```json
{"id": "utt_000003", "text": "I would like some lice with my dinner.", "words": [{"w": "lice", "t0": 2.31, "t1": 2.62, "p": 0.41}, …],
 "phones": [{"ph": "l", "t0": 2.34, "t1": 2.36, "p": 0.93}, {"ph": "aɪ", "t0": 2.44, "t1": 2.46, "p": 0.97}, …],
 "phones_model": "wav2vec2-lv-60-espeak-cv-ft", "phones_source": "model"}
```

- `ph` は認識器の IPA（eSpeak の表記。`aɪ` `tʃ` `ɚ` は 1 つ）。`t0`/`t1` はセッション秒で、CTC の「その音素が出た 20 ms の枠」。
  区間の長さに意味は無い（点として扱う）。`p` は認識器の確率の転記。
- 語の区切りは持たない（どの音素がどの語かは、時刻で `words` と突き合わせるか、下の整列結果の `w` を使う）。
- `phones_source` は通常の run では `model` だけ。`human`（人間の転写）・`g2p`（文字からの辞書変換）は参照 run にだけ現れる。
- 認識器は `phones.provider` で差し替える（`wav2vec2_espeak` が既定、`allosaurus` も可。違いは REPORT_段6）。

### 4.2 参照 run（`ref` / `ref_g2p`）の作り方

学習者が読むべき文が分かっているとき（音読課題など）、その文を参照にする。

```bash
python tools/ref_to_run.py <session> ref.json            # ref.json = {"group": "...", "lines": [{"t0", "t1", "text", "phones"?}]}
```

- `text` は G2P（g2p_en。CMUdict、同形異音語は品詞で読み分け）で `ref_g2p` に。「辞書上はこう読むはず」であって観察ではない。
- `phones`（人間の IPA）があれば `ref`。
- `t0`/`t1` を省くと録音全体。`--locate-run phones` で、文が読まれた範囲を Whisper の単語列から探す。
- 書いた後、同じセッションの音素つき run を自動で整列する。参照を後から作っても、`python -m intake align <session>` でやり直せる。

### 4.3 整列結果 `runs/<run>/trace/phones_align.jsonl`

1 行 = 参照 run の発話 1 つ。発話レコードではない（閲覧画面と Lumi の材料）。

```json
{"ref_run": "ref_g2p", "group": "japanese", "ref_utt": "utt_000000", "t0": 0.5, "t1": 25.3, "ref_source": "g2p",
 "ref_text": "Please call Stella. …", "hyp_utts": ["utt_000000", "utt_000001"], "phones_model": "wav2vec2-lv-60-espeak-cv-ft",
 "counts": {"match": 190, "sub": 22, "del": 5, "ins": 4},
 "ops": [{"op": "sub", "ref": "R", "hyp": "L", "ref_ph": "R", "hyp_ph": "l", "w": 12, "t0": 4.06, "t1": 4.08, "p": 0.88},
         {"op": "del", "ref": "Z", "hyp": null, "ref_ph": "Z", "hyp_ph": null, "w": 20, "t0": 16.7, "t1": 16.8, "pos": "gap"}, …],
 "words": {"counts": {…}, "ops": [{"op": "match", "ref": "bring", "hyp": "bring", "t0": 3.9, "t1": 4.2, "p": 0.97}, …]},
 "table": "classes.yaml"}
```

- `op`：`match`（一致）/ `sub`（置換）/ `del`（参照にあって認識に無い）/ `ins`（認識にだけある）。粗いクラス（4.4）の上で比べる。
- `ref`/`hyp` はクラス、`ref_ph`/`hyp_ph` は元の表記。`w` は参照の text を空白で切った何番目の語か。
- `t0`/`t1` は認識側の音素の時刻。`del` は前後の音素の間（`pos: "gap"`、推定位置）。
- `words` は参照の文と Whisper の単語列の整列（Whisper が文から逸れたか）。

**Lumi の推察表の書き方（例）**：`sub` の行を材料に「誤りの候補」を書く。`refs` は `hyp_utts` の発話 id。

```json
{"id": "inf_0001", "refs": ["utt_000001"], "kind": "pron_error_candidate",
 "value": {"word": "bring", "expected": "R", "heard": "L", "t0": 4.06, "t1": 4.08},
 "by": "lumi/pron-v1", "conf": 0.6, "note": "speech-intake phones_align（ref_g2p 比）の sub。英語母語の対照群でも多い対なら conf を下げる"}
```

### 4.4 粗い音素クラス表 `intake/phones/classes.yaml`

ARPAbet の 39 音素＋`DX`（弾き音）＋`Q`（声門閉鎖）の 41 クラス。認識器の IPA・人間の狭い IPA・G2P（ARPAbet）を同じクラスに丸める。
補助記号は落とし、二重母音・破擦音は 1 クラス。表は YAML で直せる（直したら `python -m intake align` で整列し直す）。
Lumi のスキルツリーの軸を切るときの最小単位に使える（例：L/R、TH/S/T、V/B、語末の Z/S）。

### 4.5 3 コーパス（実際は 2）で見えた、認識器の得意・不得意

Common Voice は要アカウントのため見送り。SAA（日本語話者 44 人＋英語母語 44 人）と IDEA（日本語話者 17 人）で見た。
数は REPORT_段8・段9。

- **拾える**：L↔R（日本語話者で G2P 比 L→R 45・R→L 21、英語母語では L→R 1）、TH→S/T、語末の Z→S、S→SH。
  合成の誤読でも rice→lice・read→lead・sea→she・thank→sank は置換として出た。Whisper はこれらの語を正しい綴りで書いていることが多い。
- **拾いにくい**：TH を S にした誤読を θ と聞くことがある（think→sink は出ず）。V→B は合成では出たが SAA では 1 件だけ。
  母音の質の差は多く出るが、英語母語の対照群にも同じくらい出る（AH→AE・IY→EY・UW→AH）→ 認識器か G2P のくせ。
- **誤りではない差が混ざる**：米語の弾き音（letter の t → `DX`）、G2P の読み（文脈の読み分けは品詞まで）、人間の転写者の流儀。
- **使い方の勧め**：差の数をそのまま点数にしない。同じ文を英語母語の話者が読んだときにも多い対かどうか（閲覧画面の「群の集計」、
  `tools/align_report.py`）で、学習者に特有の差かを見分ける。人間の IPA との一致率は wav2vec2 で日本語話者 77.6%・英語母語 86.6%。
