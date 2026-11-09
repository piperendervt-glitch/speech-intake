# REPORT 段8：Speech Accent Archive（日本語話者全員＋英語母語の対照群）

## 到達点

日本語話者と英語母語話者で、認識した音素と人間の IPA の差分を並べて見られる。Whisper の書き起こしが原文から
どれだけ逸れるかも同じ画面（「整列」の表の単語の行）で分かる。

```bash
python tools/import_saa.py --list
python tools/import_saa.py --japanese all --native same --replay -c config/phones.yaml
python -m intake view          # 群=japanese / native で絞る。参照=human（ref）か g2p（ref_g2p）。下に群の集計
python tools/align_report.py --run phones --ref ref --corpus saa     # 群ごとの数（Markdown）
```

## 取得

- 配布元のサイト（accent.gmu.edu）は作り直されていて、Download ページから OSF に案内される。
  **OSF（osf.io/yh23d）はログイン不要**で、API で一覧も個別の取得もできた（5541 件：mp3 3016・転写テキスト 1266・gif 1259）。
  Kaggle の鏡像は転写を含まない（mp3 と speakers_all.csv だけ）、DagsHub は API がログインを求めた → OSF を使う。
- **転写はテキストで取れた**（依頼文 §3.6 の「画像のみ」には当たらない）。日本語話者のファイルは見出し行（例「Japanese speaker 1」）の後に `[IPA]` が続く形。
- 日本語話者：mp3 44 人、うち精密 IPA のある人 16 人（ほかは "no transcription yet"）。全員を取り込み、転写のない 28 人は `ref_g2p` だけ。
- 英語母語の対照群：転写のある人を番号順に 44 人（OSF の english は mp3 659・txt 185）。
- 置き場所は `data/saa/`（git 対象外）。88 セッションの取得と phones の replay で 17.5 分。

## 数（数えただけ。合否は決めない）

参照あたりの音素（粗いクラス）。一致＋置換＋脱落 が参照の音素数。

**認識（wav2vec2）vs 人間の IPA（ref）**

| 群 | 人 | 参照の音素 | 一致 | 置換 | 脱落 | 挿入 |
|---|---|---|---|---|---|---|
| japanese | 16 | 3572 | 2773（77.6%） | 678 | 121 | 100 |
| native | 44 | 9772 | 8467（86.6%） | 921 | 384 | 181 |

allosaurus（eng）は同じ 16 人ずつで japanese 67.1%・native 73.8%（既定を wav2vec2 にした理由の 1 つ）。

**認識（wav2vec2）vs G2P（ref_g2p）、Whisper vs 原文の単語**

| 群 | 人 | 音素：一致 | 置換 | 脱落 | 挿入 | 単語：一致 | 置換 | 脱落 | 挿入 |
|---|---|---|---|---|---|---|---|---|---|
| japanese | 44 | 8319 | 1196 | 253 | 288 | 2889 | 135 | 12 | 22 |
| native | 44 | 8764 | 708 | 296 | 97 | 2982 | 40 | 14 | 22 |

Whisper は日本語話者の朗読でも原文の 95.2%（native 98.2%）の語をそのまま書いた。音素の方は日本語話者の置換が native の 1.7 倍。

## 見えたもの（G2P 比で、日本語話者に多く native に少ない置換）

Whisper が語を正しく書いた箇所で、音素では置換になっていた例（セッションは `20261109_…_saa-japaneseN`）：

| 対 | japanese | native | 例 |
|---|---|---|---|
| L→R | 45 | 1 | japanese3「Stella」2.05 秒、「slabs」12.81 秒 |
| R→L | 21 | 上位に無い | japanese2「bring」4.06 秒、japanese4「red」32.51 秒 |
| Z→S（語末の z の無声化） | 59 | 4 | japanese1「cheese」16.64 秒 |
| TH→S / TH→T | 24 / 9 | 4 / 4 | japanese4「things」5.00 秒、japanese17「thick」12.05 秒 |
| DH→D / DH→Z | 15 / 10 | 少数 | japanese4「brother」21.16 秒 |
| B→V | 16 | 上位に無い | （v と b の取り違えの逆向き。確かめるなら閲覧画面で） |
| V→B | 1 | — | japanese40「of」14.50 秒 |

両方の群に同じくらい出る対（AH→AE 119/116、IY→EY 44/44、UW→AH 44/75 など）は、認識器か G2P のくせとみられ、
日本語話者に特有の差ではない。**群どうしで比べて見るのが大事**（閲覧画面の「群の集計」）。

## 閲覧画面

- 群（`group`：japanese / native）でセッションの一覧を絞れる。参照 run の run.json と session.json に入れている。
- 「群の集計」：選んでいる run と参照について、群ごとのセッション一覧（行をクリックでそのセッションへ）と、多い置換・脱落・挿入の対。

## 気になる点

- 集計に `?˺`（開放なしの印 ˺ が単独の音素として切られていた）と `?5` が出ていた → 表と切り方を直した（`python -m intake align` で整列し直すと消える。上の数は直す前のもの）。
- 人間の転写は判定者の表記の流儀（狭い／広い）に揺れがある。参照の音素数が同じ文でも人によって違う（japanese 1 人あたり 約 223、native 約 222）。
