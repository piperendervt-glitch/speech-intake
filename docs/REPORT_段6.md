# REPORT 段6：schema 0.2（phones）＋ 音素認識 2 種 ＋ 閲覧画面の音素レーン

## 到達点

英語の録音を流すと、発話ごとに Whisper の単語列と、音素認識器の音素列（IPA、時刻と確率つき）が並ぶ。

```bash
pip install -e .[phones,g2p,samples-en]            # torch は GPU 版を別に入れる（README）
pip install allosaurus                              # 差し替え用（任意）
python -m intake -c config/phones.yaml models phones   # wav2vec2（約 1.2 GB、トークン不要）。初回の実行でも自動で取る
python tools/make_samples.py --name en_read         # 英語の合成サンプル（Kokoro af_heart）
python -m intake import samples/en_read_kokoro.wav --label en-read
python -m intake -c config/phones.yaml replay <session>                                          # run「phones」
python -m intake -c config/phones.yaml --set phones.provider=allosaurus --set phones.model=uni2005 replay <session> --run phones_allo
python -m intake view    # 「音素」をオンに。単語の下に音素が時刻つきで並ぶ
```

## 作ったもの

- **schema 0.2**（`intake/schema`、`docs/schema/utterance.schema.json`）
  - `Utterance.phones: list[Phone]`、`phones_model`、`phones_source`（`model` / `human` / `g2p` / `none`）
  - `Phone = {ph, t0, t1, p, w}`。`ph` は IPA、時刻はセッション秒、`p` は認識器の確率の転記。
    `w`（text を空白で切った何番目の語か）は参照だけが持つ。参照（human / g2p）は時刻を持たないことがある（null）。
    `phones_source: model` の音素は時刻が必須（検証で落とす）
  - 0.1 のレコード（欄が無い）は空として読む（テストあり）
- **phones provider**（`intake/phones/`、`registry.py` の段 `phones`）
  - `wav2vec2_espeak`：`facebook/wav2vec2-lv-60-espeak-cv-ft`。CTC を自前で貪欲に復号し、音素が出た枠（20 ms）から時刻、
    枠の最大確率の平均から `p` を出す。transformers の tokenizer（phonemizer と espeak-ng が要る）は使わず vocab.json だけを読む
  - `allosaurus`：`uni2005`、`lang: eng`（英語の音素目録）か `ipa`（全言語共通）。timestamp 出力を読み、第 1 候補の確率を `p` に
  - パイプラインでは STT の後に同じ VAD 区間で動き、音素の中点が入る発話に振り分ける。STT が何も出さなかった区間の音素は
    `trace/phones_orphan.jsonl` にだけ残す。GPU は STT 優先、`phones.device: cpu` で CPU に落とせる
- `config/phones.yaml`：VAD ＋ STT（`language: en` 固定、`one_per_vad: true`）＋ phones
- 閲覧画面：音素レーン（認識器の音素、確率で濃淡）、表記の切り替え（元の IPA ／粗いクラス）、単語の `p` を濃淡で出す切り替え、
  音素をクリックするとその前後 0.15 秒だけ再生

## 確認（ユーザーの PC：Windows 11、RTX 5060 Ti 16 GB、Python 3.12）

- 導入：`pip install -e .[phones,g2p,samples-en]` は 18 秒でエラー無し。`pip install allosaurus` は 40 秒（numba・scipy・panphon などが入る。
  torch 2.11+cu128 は置き換えられない）。どちらもトークン不要。
- 英語の合成サンプル `samples/en_read_kokoro.wav`（18.6 秒）と `en_misread_kokoro.wav`（18.7 秒、わざと誤読）で、
  単語の下に音素が時刻つきで並ぶことを確かめた。
- 所要時間（STT large-v3 込み、1 run）：wav2vec2 2.5〜2.7 秒、allosaurus 3.1〜3.2 秒。wav2vec2 は cuda で 18.7 秒の音声を 0.25 秒、VRAM 最大 約 1.6 GB。

## 既定は wav2vec2_espeak

どちらも導入は素直に通った。決め手は出力の形と、誤読の拾い方（段7 の誤読サンプル）：

| | wav2vec2_espeak | allosaurus |
|---|---|---|
| 依存 | torch・transformers（約 1.2 GB のモデル） | torch・numba・scipy・panphon（モデル 約 46 MB） |
| 表記 | eSpeak の音素（`aɪ` `oʊ` `tʃ` `ɚ` を 1 トークンで出す） | 汎用の音素目録。二重母音を `a`＋`j` のように**わたり音に割って**出す |
| 時刻 | CTC の 20 ms の枠（点に近い） | 同じく枠単位 |
| 誤読 10 か所の置換 | **9 か所**（think→sink だけ θ と認識） | eng 6〜7 か所（段7 で表を直した後） |
| 誤読の無い読み上げでの置換（ref_g2p 比） | 5 | 7 |
| SAA で人間の IPA と一致した割合（参照の音素あたり） | 日本語 16 人 77.6%、英語母語 44 人 86.6% | 日本語 16 人 67.1%、英語母語 16 人 73.8% |

allosaurus は軽く、全言語の目録（`lang: ipa`）で日本語らしい音（`b̥` など）を出せるのが利点。差し替えは `phones.provider`。

## 外部送信

`python -m intake view` を動かした状態で `python -m intake netcheck`：`OK 127.0.0.1:8765 → すべて localhost のみ`（PC）。

## 未解決点

- CTC の時刻は「その音素が出た点」で、区間の長さは意味を持たない。閲覧画面では次の音素の頭までを幅として描いている。
- allosaurus の `uni2005` は LOG の見込み（20 MB）より大きく 46 MB だった（LOG を訂正済み）。
