# REPORT 段9：IDEA の取り込み・Common Voice の見積もり・申し送り

## 到達点

SAA と IDEA の 2 コーパスが同じ閲覧画面で見られる（Common Voice はユーザーの判断で見送り）。Lumi 向けの申し送りは
`docs/INTEGRATION_JA.md` §4。

```bash
python tools/import_idea.py --pages 1-17 --replay -c config/phones.yaml
python tools/align_report.py --run phones --ref ref_g2p --corpus idea
```

## IDEA（dialectsarchive.com）

- 利用条件（サイトの Terms of Use）：個人の研究目的での利用は可。音声の再配布は不可（書面の許可と料金）、テキストは短い引用まで。
  → `data/idea/` にだけ置き、リポジトリにも外部にも出さない。台本の本文もリポジトリに入れない（取り込み時にページから取る）。
- japan-1〜17 の 17 ページ、全件。mp3 は直接取得できる。取得と replay で 9.6 分。
- 録音は台本の朗読＋自由発話。台本は 14 件が「Comma Gets a Cure」、**japan-1〜3 は「The Rainbow Passage」の第 1 段落**。
  台本を 2 つ持たせ、STT の単語列とよく合う方を選び、読まれた範囲（t0〜t1）と読まれた文を STT から探して参照にした
  （`ref_to_run.py --locate-run --trim-to-read`）。Comma は 6〜15 秒から始まって 150〜259 秒、Rainbow は 35〜52 秒。
- 精密な音声表記（テキスト）は japan-12〜16 の 5 件（台本の最後まで）→ run `ref`。全 17 件に `ref_g2p`。
  自由発話の綴りの書き起こしは `data/idea/<page>/free.txt` に残した（音素の参照は作らない）。

| 参照 | 人 | 参照の音素 | 一致 | 置換 | 脱落 | 挿入 | 単語：一致 | 置換 | 脱落 | 挿入 |
|---|---|---|---|---|---|---|---|---|---|---|
| ref（人間の IPA） | 5 | 6765 | 4687（69.3%） | 1664 | 414 | 374 | 1635 | 226 | 19 | 99 |
| ref_g2p | 17 | 18969 | 15013 | 3276 | 680 | 1087 | 5006 | 486 | 53 | 163 |

IDEA は SAA より長く（約 380 語）、言い直しや読み飛ばしがある（japan-14 は単語の置換 104）。範囲の探し方も一致が密に続く
部分だけで決めるよう直した（初版は後ろの自由発話まで伸び、単語の挿入が 403 になっていた）。

## Common Voice 英語（accent＝Japanese）

`docs/LOG.md` に見積もり。2025-10 以降は Mozilla Data Collective だけで配布され、アカウントが要る（HuggingFace は README だけ）。
accent で絞るにも英語全体（数十 GB）を落とす必要があり 1 時間を超える → **ユーザーの判断で見送り**（2026-11-09）。
`tools/import_cv_accent.py` は手元に置いた英語のデータ（展開したフォルダか .tar.gz）を読む形で残した（件数のテストあり）。

## 外部送信

PC で view を動かした状態で `python -m intake netcheck`：`OK 127.0.0.1:8765 → すべて localhost のみ`（段6・段9 とも）。
取り込みのための取得（OSF・dialectsarchive.com）は読み込みだけで、送信はしていない。

## テスト

34 件（schema 0.2 の互換、正規化表、整列の小さな例、各 importer の件数・抜き出し、朗読範囲の探索）。
