# REPORT 段7：粗い音素クラス ＋ 整列 ＋ 参照 run（human / g2p）＋ 差分の色

## 到達点

台本つきの英語合成音声で、G2P の期待音素と認識した音素の差分が色で出る。わざと誤読させた合成音声で置換が見える。

```bash
python -m intake import samples/en_misread_kokoro.wav --label en-misread
python -m intake -c config/phones.yaml replay <session>
python tools/ref_to_run.py <session> samples/en_misread_kokoro.script.json   # run「ref_g2p」を作り、整列する
python -m intake view    # 設定 A=phones、参照=g2p。音素レーンの下に参照の音素、差分の色（一致／置換／脱落／挿入）
python -m intake align <session>   # 整列だけやり直す（classes.yaml を直したときなど）
```

## 作ったもの

- **粗い音素クラス**（`intake/phones/classes.yaml`、`classes.py`）：ARPAbet の 39 音素＋弾き音 `DX`＋声門閉鎖 `Q` の 41 クラス。
  補助記号（ː ʰ ̃ ˈ ˺ など）を落とし、表を長いものから当てる（`a`+`ɪ`→`AY`、`t`+`ʃ`→`CH`、わたり音表記の `a`+`j`→`AY`）。
  ARPAbet（g2p_en）は強勢の数字を落として引く。表に無い記号は `?ʘ` のように残す（隠さない）。表は YAML でユーザーが直せる。
  参照の音素は語の区切り（`Phone.w`）をまたいでまとめない。認識側には語の区切りが無いので、語をまたいだ母音＋j/w をまとめることがある。
- **整列**（`intake/phones/align.py`）：参照の発話ごとに、その時間に入る認識側の音素（発話をまたいでよい）をクラスの列にし、
  編集距離で 一致／置換／脱落／挿入 に分ける。単語も同じ方法で、参照の text と Whisper の単語列を並べる。
  結果は認識側 run の `trace/phones_align.jsonl`（発話レコードには入れない）。形式は INTEGRATION_JA.md §4。
  `replay`（phones あり）と `ref_to_run.py` の後に自動で走る。
- **参照 run**（`tools/ref_to_run.py`。`script_to_run.py` を一般化）：入力は発話ごとの text と、あれば人間の IPA。
  人間の転写 → run `ref`（`phones_source: human`）、text の G2P → run `ref_g2p`（`phones_source: g2p`、run.json の note に
  「辞書上はこう読むはず。観察ではない」と明記）。G2P は `g2p_en`（既定。CMUdict、同形異音語は品詞で読み分け）か `espeak`。
  `--locate-run` で text が読まれた範囲を STT の単語列から探す、`--trim-to-read` で読まれた文だけに切る（段9 の IDEA で使う）。
- **閲覧画面**：参照レーン（整列で対応した位置に参照の音素、脱落は赤い印）、差分の色、参照の種類（human／g2p）の切り替え、
  「整列」の表（参照の発話ごとに 参照／認識 の音素を縦に並べ、単語の差分も色で）。音素や単語をクリックでその前後を再生。

## 確認（ユーザーの PC）

`samples/en_misread_kokoro.wav`：10 か所の誤読（text は本来の文、TTS には誤読した文を読ませた）。整列（phones vs ref_g2p）：

| 誤読 | Whisper | wav2vec2 | allosaurus（eng） |
|---|---|---|---|
| rice→lice | lice | R→L | R→L（表の修正後） |
| light→right | right | L→ER（ɚ） | L→ER（表の修正後） |
| very→berry | **very** | V→B | 出ず |
| think→sink | **think** | 出ず（θ と認識） | TH→S |
| sea→she | she | S→SH | S→SH |
| read→lead | lead | R→L | 表の修正前は R の脱落でずれた（修正後は未確認） |
| letter→retter | rudder | L→ER | L→ER |
| works→walks | walks | ER→AA | ER→AO |
| park→pork | pork | AA→AO | AA→AO |
| thank→sank | **Thank** | TH→S | 出ず（θ と認識） |

Whisper は very・think・thank を正しい綴りに直して書く（依頼文の前提どおり）。音素の方には、その位置に置換が出る（wav2vec2 で 9/10）。

数（音素 一致/置換/脱落/挿入、ref_g2p 比）：

| | en_read（誤読なし） | en_misread |
|---|---|---|
| wav2vec2 | 126 / 5 / 2 / 0 | 112 / 17 / 4 / 0 |
| allosaurus eng | 124 / 7 / 2 / 0 | 116 / 13 / 4 / 1 |
| allosaurus ipa | — | 114 / 16 / 3 / 2 |

## 直したもの（実機で見つかった）

- allosaurus は二重母音を `a`+`j` と出すため AY が AA と Y に割れ、誤読の無い文にも偽の置換が出ていた → わたり音表記を表に足した（en_read の allosaurus の置換 17→7）。
- g2p_en を語ごとに引くと read が過去形（R EH D）になっていた → 文の品詞で同形異音語を読み分ける（g2p_en と同じ表）。

## 残る「誤読ではない差」

- letter・water などの t は米語で弾き音 [ɾ] になり、wav2vec2 は `DX` と出すので、G2P（T）との差が T→DX として数えられる。
  弾き音は日本語の r とも関わるので、`DX` を T にまとめることはしていない（classes.yaml で変えられる）。
- 誤読の無い合成音声でも数か所の置換が出る（母音の質の差など）。差の数そのものではなく、群どうしで比べて見る（段8）。
