# 作業ログ（ダウンロード・長い処理の事前記録）

依頼文 §7「モデルのダウンロードや 1 時間を超えそうな処理は、見込みの時間と目的を先に書いてから実行する」に従う。

| 日時 (UTC) | 何を | 目的 | 見込み | 結果 |
|---|---|---|---|---|
| 2026-11-09 07:20 | torch（CPU 版, 約 200 MB） | Silero VAD と Kokoro TTS の実行 | 数分 | 失敗：クラウド環境から download.pytorch.org に接続できない（403）。Silero は torch 無しで onnxruntime から動かす方針に変更 |
| 2026-11-09 07:20 | Kokoro-82M（hexgrad/Kokoro-82M, 約 330 MB）と日本語 G2P（misaki[ja], unidic 約 500 MB） | samples/ の 2 話者合成会話を作る | 10 分程度 | 未実行：クラウド環境から huggingface.co に接続できない。ユーザーの PC で行う |
