# REPORT 段5：WebSocket 配信 + speaker.wearer スタブ + 利用側への申し送り

## 到達点

別プロセスが発話レコードを購読できる。

```bash
python -m intake -c config/stream.yaml record                                     # ライブで配信
python -m intake -c config/stream.yaml replay <session> --speed realtime --hold   # 録音を流し直して配信
python tools/subscribe.py --out records.jsonl                                     # 購読の例
```

利用側への申し送りは `docs/INTEGRATION_JA.md`（face-track-map の購読手順と推察表の書き方、subvox 向けの区間形式と教師データの取り出し、Lumi Bench 向けの関数境界）。

## 確認

- ユーザーの PC（stream.yaml、stt=small、realtime）：発話が流れ終わった後に接続しても、それまでの発話 9 件・revise・end をまとめて受け取れた。revise 後の話者は spk_1, 2, 1, 2, 2, 1, 2, 1, 2 で、台本と一致。
- クラウドとユーザーの PC の両方で、閲覧画面と配信を動かした状態で `python -m intake netcheck` を実行し、待ち受けが localhost だけなことを確かめた。
  ```
  OK  127.0.0.1:8766 （replay --hold）
  OK  127.0.0.1:8765 （view）
  → すべて localhost のみ
  ```
  設定に localhost 以外の host を書くと、起動時に拒否する（テストあり）。
- テスト：21 件すべて成功（クラウドと PC）。

## 確定事項

- 配信は `ws://127.0.0.1:8766/ws`。メッセージは `hello` / `utterance` / `revise` / `trace`（`?trace=1` のときだけ）/ `end`。途中から接続した購読者にも、その run のそれまでの発話が先に届く。
- `speaker.wearer`（スタブ）は判定をしない。外部から「装着者が話していた区間」をファイル（`sessions/<session>/wearer.jsonl`）か WebSocket（`{"type":"wearer",...}`）で受け取り、重なりの割合で `wearer` / `not_wearer` を付ける。
- マイク録音のセッションは `session.json` に `t0_unix`（最初のサンプルの壁時計、概算）を書く。外部プロセスの時刻をセッション秒に直す手がかりにする。
- 教師データの取り出しは `tools/export_vad_teacher.py`（一人で話した収録の VAD 区間を、壁時計つきで JSONL に書く）。

## PC での確認で見つかって直したもの

- **同梱の Silero VAD モデルが GitHub に入っていなかった**：`.gitignore` の `models/` が `intake/vad/models/` にも当たっていた。除外をルートの `/models/` だけにした（それまでは clone しただけでは VAD が動かなかった）。
- `netcheck` が Windows で待ち受けを見落としていた：psutil が状態を LISTEN でなく NONE と返すため。相手の無い TCP ソケットも数えるようにした。
- faster-whisper が取得済みでも起動のたびに HuggingFace へ版を問い合わせていた：手元にあればネットに問い合わせないようにした（REPORT_段3）。
- `intake models whisper` の置き場所が STT の読む場所と違っていた：HuggingFace の既定キャッシュにそろえた。
- Kokoro でサンプルを作り直すには `python -m unidic download` が別に要る：README に書いた。

## 未解決点

- ライブ中の話者付け（REPORT_段4）。
- wearer 区間の時刻合わせは壁時計の概算（OS のバッファ分、数十 ms ずれうる）。subvox 側の実装と合わせて詰める。
- 顔による話者ラベル（`speaker_source: "face"`）は型の上で場所を用意しただけ。
- ユーザー自身の録音（subvocal-robot の録音を含む）での本番の確認。
