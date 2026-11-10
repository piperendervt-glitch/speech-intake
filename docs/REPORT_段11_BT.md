# REPORT 段11（その 2）：Bluetooth ヘッドセットの入力と機器の診断

依頼文：`docs/04_REQUEST_bt_headset_JA.md`（v0.4）。段11 の前半（turn-bench の申し送り、JA1 入力）は `docs/REPORT_段11.md`。
名前がぶつからないよう、この報告は別のファイルにした。

## ユーザーが試す手順（PC、実機が要る）

クラウドには実機が無いので、以下は未確認。`C:\dev\speech-intake` で `git pull` してから（stash@{0} はそのまま）。

1. AirPods をペアリングしてケースから出し、耳に着ける。
2. `python -m intake devices` … AirPods の行が API ごとに並ぶ。`開` が ○ か △ の **Hands-Free の行**（まず MME）を選ぶ。
   一覧をそのまま送ってもらえれば、どの行がよいか一緒に見る。
3. `python -m intake devcheck --device "AirPods@MME" --label airpods` … 8 秒。始まったら 1 秒黙ってから、画面の英文を読む。
   判定（広帯域らしい／狭帯域らしい／判断しにくい）と数字が出る。
4. `python -m intake devcheck --device "<PC のマイクの名前の一部>@MME" --label pcmic` … 同じ英文を PC のマイクで。
5. `python -m intake view` … セッション `…_airpods` を開き、「比較のセッション」に `…_pcmic` を選び、「スペクトログラム」をオン。
   4 kHz の点線より上に s・sh の筋があるかを見る（**ここで広帯域か狭帯域かを決める**）。
6. `python -m intake -c config/live.yaml --set capture.device="AirPods@MME" record` を起動し、別の窓で
   `python tools/subscribe.py --events`。何か話して、`speech_start` → `speech_end` → `utterance` が届くかを見る。
   同じ内容を PC のマイク（`--set capture.device="…@MME"`）でも録り、閲覧画面で並べて文字と単語の `p` を比べる。
7. （任意）`python tools/latency_pair.py --a "<PC のマイク>@MME" --b "AirPods@MME"` … 15 秒、2〜3 秒おきに手を叩く。
   「B は A より N ms 遅い」が出る。閲覧画面でセッション `…_pair-a` を開くと B が並び、叩いた所のずれが見える。
8. （任意）録音中に AirPods をケースにしまう … 2 秒ほどで「機器から音が届かなくなった」と出て、それまでが保存されるか。

返してほしいもの：2 の一覧、3・4 の出力、5 の見た目（広帯域か狭帯域か）、7 の N。

## 到達点

| 依頼 | 対応 |
|---|---|
| §3.1 一覧に機器名・ホスト API・既定のレート・チャンネル数、マイクとして使える行の印 | `devices`：`開`（○ 16 kHz で開ける／△ 既定のレートでなら開ける／× 開けない。PortAudio の `check_input_settings` で確かめるだけで録らない）と `BT`（名前から Bluetooth らしい）。`--json` も |
| §3.1 `capture.device` を名前の一部で | 番号・名前の一部・`"名前@ホストAPI"`。`capture.hostapi` / `--hostapi` でも絞れる。複数当たったら候補と絞り方を出して止まる |
| §3.1 16 kHz 以外は 16 kHz に変換して保存、元のレートを session.json に | 既定は「16 kHz で開き、断られたら機器の既定のレートで開いて直す」（`capture.device_rate`：`native` / 数でも）。`session.json` に `device_rate`（開いたレート）・`device_default_rate`（申告）・`resampled`・`device_name`・`hostapi` |
| §3.1 録音中に機器が消えたら保存して分かるメッセージで終わる | `capture.stall_s`（既定 2 秒）音が届かない／ストリームが止まった／読み取りの例外で止める。`session.json` に `ended_by: "device_lost"` と `lost`（説明） |
| §3.2 `devcheck` | `python -m intake devcheck [--device 名前] [--seconds 8] [--label X]`。`--analyze SESSION` で録らずに既存のセッションを診断。`devcheck.json` と閲覧画面に、帯域（中身で見る）・頭の欠け・雑音・途切れ・平均スペクトル |
| §3.2 読む英文を画面に | 録る前に 3 文を出す（README にも） |
| §3.2 判定は 3 つまで、人が決める | 「広帯域らしい／狭帯域らしい／判断しにくい」。数字は目安とし、合否は出さない |
| §3.3 スペクトログラム | 閲覧画面の「スペクトログラム」（波形の下、0〜8 kHz、4 kHz に点線）。見えている範囲だけサーバで計算する |
| §3.3 2 つのセッションを上下に | **既存の仕組みは同じセッションの run A / B の比較だけだった**ので、「比較のセッション」を足した（B に別のセッションの波形・スペクトログラム・run を出す）。時刻は録音開始の壁時計で合わせる（「B のずらし」で手でも） |
| §3.4 `tools/latency_pair.py` | 2 つの機器で同時に録り、手を叩いた音の立ち上がりの相互相関で、壁時計で合わせた後に残るずれ（B が A より何 ms 遅いか）を出す。1 回ずつのずれも。結果は両方の `session.json` の `pair` と A の `latency_pair.json` |
| §3.4 `capture.latency_ms` で補正 | `speech_start` / `speech_end` に `t_said_unix`（下の確定事項） |
| §3.5 文書 | README「Bluetooth ヘッドセットを使う」、INTEGRATION §5.6 |

## 確定事項

- **`t_unix` は書き換えず、`t_said_unix` を足した**。依頼文は「`t_unix` の補正に使う」だが、`t_unix` は段10 から「配った時刻」で、
  turn-bench・`subscribe.py`・`live_latency.py` がその意味で使っている。そこで「話した時刻の見積もり」を別の項目にした：
  `t_said_unix` = セッション 0 秒の壁時計（音声が届いた時刻から逆算。段10 の `t_emit` と同じ）＋ 区間の端 − `latency_ms`。
  本物の入力（`record`、マイクと JA1）のときだけ付ける（流し直しには話した時刻が無い）。
- **`latency_pair.py` の値の意味**：A（PC のマイク）に対して B が何 ms 遅れて**届く**か。A 自身の遅れは含まない（差だけ）。
  両方の `t0_unix`（最初のブロックが届いた時刻 − その長さ）で合わせるので、`speech_start` の `t_unix` に乗る遅れと同じ物差し。
  PortAudio のバッファの差も含む（それも `t_unix` に乗るので、補正の値としてはそれでよい）。
- **devcheck の帯域は中身で見る**。声らしい枠（雑音より 15 dB 以上）の平均スペクトルから：上端（雑音より 6 dB 以上大きい最も高い周波数）、
  4 kHz を挟んだ段差（3.2〜3.8 kHz と 4.4〜6 kHz の差）、4 kHz より上と下の比、4 kHz より上が下より強い枠の数（s・sh など）。
  目安の文言の決め方：段差 25 dB 以上・s の枠が 0・上端 4.4 kHz 以下（または上の比 −45 dB 以下）なら「狭帯域らしい」、
  上端 5.5 kHz 以上かつ s の枠 3 つ以上なら「広帯域らしい」、他は「判断しにくい」。声が 0.5 秒未満も「判断しにくい」。
- 頭の欠け：先頭から −70 dBFS 未満の 10 ms 枠が続いた長さ。途切れ：その後ろで −85 dBFS 未満（ほぼデジタルの無音）が 20 ms 以上続いた所。
- **リサンプルは numpy だけ**（窓つき sinc、Kaiser 窓。`intake/resample.py`）。scipy・soxr を取り込みの依存に足さないため。
  48 kHz → 16 kHz の遅れは約 1 ms。ブロックの切れ目で結果は変わらない（テストで一括と一致）。
- 既定の開き方は段10 までと同じ（まず 16 kHz で開く）。MME は Windows がレートを変えてくれるので、PC のマイクの今の動きは変わらない。
  WASAPI のように 16 kHz を断る行だけ、機器のレートで開いて speech-intake が直す。`devcheck` は既定で機器の既定のレート（`--rate native`）で開く
  （申告どおりのレートで録る。`--rate auto` で record と同じ開き方）。
- **PR #3（JA1）との関係**：AirPods の入口が 2 つになった。
  `record --input ja1` は subvocal-robot の airpods.bat（自前の HFP 取り込み）が UDP で送る音を受ける口、
  今回の `capture.device` は Windows 標準の Hands-Free の行を直接開く口。どちらも 16 kHz の `audio.wav`・同じ通知になる。
  `latency_ms` と `t_said_unix` は JA1 にも効く。`devcheck` は JA1 では録れない（`--analyze` で JA1 のセッションを診断はできる）。

## 確認（クラウド）

- テスト `tests/test_stage11_bt.py`（13 件）：名前での機器指定（@ での絞り込み・複数当たりの候補・無い名前・無い番号）、
  リサンプル（48k / 44.1k / 8k → 16k を細切れで流して一括と一致、1 kHz の正弦波の誤差 1e-3 未満、10 kHz の折り返しが消える）、
  機器のレートで開いて 16 kHz で保存・`device_rate` の記録、16 kHz を断られたときに既定のレートで開き直す、機器が消えたとき
  （ブロックが止まる → 0.3 秒で止まり、それまでが保存され `ended_by: device_lost`）、devcheck（合成の 16 kHz 相当と 8 kHz 相当、
  頭の欠け・途切れ・雑音の値、声の無い録音は「判断しにくい」、devcheck.json）、latency_pair（壁時計の差 −0.4 秒＋遅れ 180 ms の
  合成の拍手を 180 ms と測る）、`t_said_unix`（時計を差し替えて、`latency_ms` 150 のときの値と、流し直しでは付かないこと）。全体 64 件が通る。
- 合成の 2 本（`samples/en_read_kokoro.wav` に拍手を足し、B は 8 kHz 相当にして 180 ms 遅らせ、頭を 400 ms 無音に）で通し：
  `devcheck --analyze` は A「広帯域らしい（上端 7781 Hz、s の枠 67）」、B「狭帯域らしい（上端 3969 Hz、段差 67 dB、頭の欠け 400 ms）」。
  `latency_pair.py --analyze` は「B は A より 180 ms 遅い」。閲覧画面で B が自動で並び、スペクトログラムで B の 4 kHz より上が黒く、
  拍手が 180 ms ずれて見えることを確かめた（画面の写しはリポジトリに入れていない）。
- 試していない：本物のマイク・Bluetooth（PortAudio の無いクラウドでは `devices` も動かない）。

## 未解決点

- 実機の結果（上の手順）。特に、このアダプタで広帯域になるか、遅れが何 ms か、頭の欠けの長さ。
- 狭帯域のときに Whisper の誤りがどれだけ増えるか（PC のマイクとの比較）。VAD の threshold が HFP の音で合うか。
- `devcheck` の目安の境目（段差 25 dB、上端 4.4 / 5.5 kHz）は合成音でしか確かめていない。実機の結果で見直すことがある。
- `BT` の印は名前だけで決めている（Hands-Free / Headset / AirPods など）。名前に出ない機器は印が付かない。
