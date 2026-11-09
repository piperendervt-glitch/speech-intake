# REPORT 段3：faster-whisper + 文字と単語時刻の表示

## 到達点

区間ごとに文字が出て、クリックで聞き比べられる。

```bash
python -m intake -c config/whisper.yaml replay <session>                                   # run「whisper」
python -m intake -c config/full.yaml --set stt.model=large-v3-turbo replay <session> --run full_turbo
python -m intake view    # 文字・単語時刻の表示を切り替え、区間や単語をクリックで再生
```

## 確認（ユーザーの PC：Windows 11、RTX 5060 Ti 16 GB、Python 3.12）

- faster-whisper は cuda / float16 で動いた（ctranslate2 4.8.5、pip の nvidia-cublas-cu12・cudnn 9 の DLL を自動で読み込み）。
- 処理時間（wall_s。音声の長さ kokoro 43.7 秒 / espeak 100.4 秒）

  | 音声 | VAD + large-v3 | + 話者分離 | VAD + large-v3-turbo + 話者分離 |
  |---|---|---|---|
  | kokoro | 4.0 秒 | 8.95 秒 | 7.28 秒 |
  | espeak | 38.4 秒 | 49.4 秒 | 26.2 秒 |

- Kokoro の会話（large-v3）：台本 9 行に対して発話レコード 10 件。B の 1 行（「三社あります。いちばん大きいのは…」）が VAD で 2 区間に分かれた。
  誤りは「何か→なんか」「未入金→のみ入金」「商事→昌司」程度で、ほかは台本と一致。
- large-v3-turbo：分かれた 2 件を 1 件にまとめた。「承知しました→掃除しました」「二か月→に、か月」の誤りがあり、この会話では large-v3 の方が正確。速さは espeak で約 2 倍。
- eSpeak の会話は漢字が「Chinese letter」と読まれる音声なので、どちらのモデルでも意味のない文字になる（STT の確認には使えない。VAD の確認用）。

## 確定事項

- `stt.provider: faster_whisper`。発話レコードの `stt` 欄は `faster-whisper-<モデル名>`。
- Whisper 内蔵の VAD は使わない（区間は intake.vad が決める）。`condition_on_previous_text=false`（区間ごとに独立させ、比較しやすくする）。
- Whisper が区間の中で文を区切ったときは、その区切りごとに発話レコードを分ける（`one_per_vad: true` で区間 1 つにつき 1 件）。
- 単語の時刻（`words[].t0/t1`）はセッション秒。区間の外にはみ出さないよう切り詰める。`p` は Whisper の単語確率をそのまま転記。
- device: auto で GPU があれば cuda/float16、無ければ cpu/int8。Windows では pip の nvidia-cublas-cu12 / nvidia-cudnn-cu12 の DLL を自動で通す。
- **モデルは初回だけ HuggingFace から取得し（トークン不要）、2 回目以降は手元のファイルだけを使う**（まず local_files_only で開き、無いときだけ取得する）。
  PC での確認中、取得済みでも起動のたびに版の問い合わせで HuggingFace（CloudFront）に接続していたので、これを止めた。
  `stt.local_files_only: true` にすると取得もしない（完全にオフライン）。音声や文字が外に出ることはない。
- 既定モデルは large-v3（この会話では turbo より誤りが少ない）。速さが要るライブでは `--set stt.model=large-v3-turbo` や `small` を選ぶ。

## 未解決点

- 実際の会話（ユーザーの録音、subvocal-robot の録音）での確認はまだ。相づち・言いよどみ・重なり、雑音の所での幻聴（無音に「ご視聴ありがとうございました」等が出る現象）は目で確かめてほしい。
- VAD が 1 文を 2 区間に分けることがある（min_silence_ms=300）。比較表示で 300 と 500〜800 を見比べて既定値を決めたい。
