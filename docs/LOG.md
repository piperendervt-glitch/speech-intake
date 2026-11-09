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
| 2026-11-09 14:10 | transformers・huggingface_hub（PyPI）と wav2vec2-lv-60-espeak-cv-ft（HuggingFace facebook/…, 約 1.3 GB, トークン不要） | 段6 の音素認識（phones.provider=wav2vec2_espeak） | 5〜10 分 | 成功（22:44〜22:46 JST）。transformers 5.22.2・huggingface_hub 2.4.0 は既に入っていた（pip install -e .[phones,g2p,samples-en] 全体で 18 秒）。`models phones` でモデル取得 15.7 秒、キャッシュ 1.2 GB（pytorch_model.bin 1,263,535,127 B）。トークン無しで取得できた（Windows は symlink 不可の警告のみ）。cuda で動作（18.7 秒の音声を 0.25 秒、VRAM 最大 約 1.6 GB） |
| 2026-11-09 14:10 | allosaurus（PyPI。依存に panphon・resampy など）と uni2005 モデル（約 20 MB, 初回実行時に allosaurus が取得） | 段6 の音素認識（phones.provider=allosaurus） | 数分 | 成功（22:44〜22:45）。allosaurus 1.0.2 は `pip install allosaurus` で 40 秒。torch 2.11.0+cu128 は置き換えられなかった（--dry-run で先に確認）。追加：panphon 0.22.2・resampy 0.4.3・numba 0.68.1・llvmlite 0.50.0・scipy 1.18.1・pandas 3.1.1・editdistance 0.8.1 ほか。uni2005 は GitHub releases から取得して約 46 MB（20 MB ではない）、site-packages/allosaurus/pretrained に置かれる。cuda（device_id=0）で動作。`--set phones.provider=allosaurus` だけだと phones.model に wav2vec2 の名前が残って失敗していたので models.py / allo.py を修正 |
| 2026-11-09 14:10 | g2p_en（PyPI）と NLTK の cmudict・品詞タグ付けデータ（数 MB、初回 import 時に取得） | 段7 の参照 run ref_g2p | 1〜2 分 | 成功。g2p_en 2.1.0（依存の distance 0.1.3 は Windows でも問題なくビルド）・nltk 3.10.4・inflect 7.6.0。初回の ref_to_run で NLTK の averaged_perceptron_tagger_eng と cmudict を %APPDATA%/nltk_data に取得（数秒） |
| 2026-11-09 14:10 | misaki[en]（PyPI。spacy と en_core_web_sm 約 13 MB）・Kokoro の英語の声 af_heart | 段6・7 の英語合成サンプル（samples/en_read_kokoro.wav, en_misread_kokoro.wav） | 数分 | 成功（22:46〜22:47）。misaki 0.9.4・kokoro 0.9.4・spacy 3.8.18 は既に入っていた。初回の en_read 生成で en_core_web_sm 3.8.0 を自動取得。en_read_kokoro.wav 18.6 秒（生成 31 秒）、en_misread_kokoro.wav 18.7 秒（11 秒） |
| 2026-11-09 15:00 | Speech Accent Archive（OSF osf.io/yh23d、ログイン不要）：一覧（API）と、日本語話者全員（mp3 44 件、転写 txt）＋英語母語の対照群 44 件（転写のある人を番号順に）。1 件 約 0.6 MB、計 約 55 MB | 段8 の取り込み（tools/import_saa.py）。data/saa/ に置く（git 対象外） | 取得 数分。88 セッションを phones で流すのに 15〜20 分（whisper を毎回読み込むため 1 件 10 秒前後） | （ユーザーの PC で実行） |
| 2026-11-09 15:00 | IDEA（dialectsarchive.com）japan-1〜17 のページと mp3（1 件 約 1 MB）と台本ページ | 段9 の取り込み（tools/import_idea.py）。data/idea/（git 対象外）。個人の研究目的の範囲、再配布しない | 取得 数分、流すのに 5 分前後 | （ユーザーの PC で実行） |
