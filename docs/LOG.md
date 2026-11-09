# 作業ログ（ダウンロード・長い処理の事前記録）

依頼文 §7「モデルのダウンロードや 1 時間を超えそうな処理は、見込みの時間と目的を先に書いてから実行する」に従う。

| 日時 (UTC) | 何を | 目的 | 見込み | 結果 |
|---|---|---|---|---|
| 2026-11-09 07:20 | torch（CPU 版, 約 200 MB） | Silero VAD と Kokoro TTS の実行 | 数分 | 失敗：クラウド環境から download.pytorch.org に接続できない（403）。Silero は torch 無しで onnxruntime から動かす方針に変更 |
| 2026-11-09 07:20 | Kokoro-82M（hexgrad/Kokoro-82M, 約 330 MB）と日本語 G2P（misaki[ja], unidic 約 500 MB） | samples/ の 2 話者合成会話を作る | 10 分程度 | 未実行：クラウド環境から huggingface.co に接続できない。ユーザーの PC で行う |
| 2026-11-09 07:38 | 話者分離モデル（`python -m intake models speaker`, GitHub, 約 40 MB） | 段4 の話者分離（sherpa-onnx） | 1 分 | 成功（07:37）：github.com/…/releases/download が 504 を返し続けたため、同じ資産を GitHub API（releases/assets/{id}, Accept: application/octet-stream）経由で取得。大きさ・sha256 は models/MANIFEST.txt |
| 2026-11-09 07:40 | faster-whisper large-v3（HuggingFace Systran/faster-whisper-large-v3, 約 3 GB） | 段3/4 の STT（初回実行時に自動取得） | 5〜15 分 | 成功（07:38〜07:39, 約 1 分）。cuda / float16 で動作 |
| 2026-11-09 07:42 | torch（CUDA 12.8 版, download.pytorch.org, 約 3 GB） | Kokoro TTS の実行（RTX 5060 Ti は sm_120 のため cu128 以降が要る） | 5〜10 分 | 成功（torch 2.11.0+cu128, cuda 利用可） |
| 2026-11-09 07:42 | kokoro・misaki[ja]（PyPI）と Kokoro-82M（HuggingFace hexgrad/Kokoro-82M, 約 330 MB）・日本語辞書 | samples/conv2_ja_kokoro.wav を作る | 10 分程度 | 成功（07:44〜07:45）。misaki[ja] は入ったが、別に `python -m unidic download`（cotonoha-dic S3, unidic 3.1.0）が要った（無いと fugashi が mecabrc 無しで落ちる）。43.7 秒の wav を生成 |
| 2026-11-09 07:46 | faster-whisper large-v3-turbo（HuggingFace, 約 1.6 GB） | turbo と large-v3 の比較（run full_turbo） | 数分 | 成功（07:45, 初回の full_turbo 実行の中で自動取得。1 分未満） |
| 2026-11-09 07:46 | faster-whisper small（HuggingFace, 約 500 MB） | 段5 の WebSocket 配信確認（stream.yaml, stt.model=small） | 1〜2 分 | 取得不要（この PC の HuggingFace キャッシュに既にあった。07:48 に 1 秒で完了） |
