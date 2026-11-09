# 使用したモデル・ライブラリと出典

| 名前 | 用途 | ライセンス | 出典 |
|---|---|---|---|
| NumPy | 音声配列 | BSD-3-Clause | https://numpy.org/ |
| python-soundfile (libsndfile) | WAV 読み書き | BSD-3-Clause（libsndfile は LGPL-2.1） | https://github.com/bastibe/python-soundfile |
| python-sounddevice (PortAudio) | マイク取り込み | MIT（PortAudio は MIT 系） | https://github.com/spatialaudio/python-sounddevice |
| PyYAML | 設定ファイル | MIT | https://pyyaml.org/ |
| aiohttp | 閲覧画面のサーバ | Apache-2.0 | https://github.com/aio-libs/aiohttp |
| FFmpeg（任意。import 時の変換に使う） | 音声形式の変換 | LGPL/GPL | https://ffmpeg.org/ |
| psutil（任意。netcheck で使う） | 待ち受けソケットの確認 | BSD-3-Clause | https://github.com/giampaolo/psutil |
| Silero VAD 6.2.3（silero_vad.onnx を `intake/vad/models/` に同梱） | 発話区間の検出 | MIT（`intake/vad/models/LICENSE.silero-vad`） | https://github.com/snakers4/silero-vad |
| ONNX Runtime | Silero VAD の実行 | MIT | https://github.com/microsoft/onnxruntime |
| eSpeak NG（任意。開発用サンプル `samples/conv2_ja_espeak.wav` の合成に使用） | 開発用の合成音声 | GPL-3.0（合成した音声そのものには及ばない） | https://github.com/espeak-ng/espeak-ng |
| faster-whisper / CTranslate2 | 文字起こし | MIT | https://github.com/SYSTRAN/faster-whisper |
| Whisper（OpenAI、Systran による CTranslate2 変換版） | 文字起こしのモデル | MIT | https://github.com/openai/whisper , https://huggingface.co/Systran |
| sherpa-onnx | 話者分離（既定の acoustic） | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx |
| pyannote segmentation 3.0（sherpa-onnx の ONNX 変換版） | 話者分離の区間検出 | MIT | https://huggingface.co/pyannote/segmentation-3.0 , https://github.com/k2-fsa/sherpa-onnx/releases |
| 3D-Speaker ERes2Net base（sherpa-onnx の ONNX 変換版） | 話者埋め込み | Apache-2.0 | https://github.com/modelscope/3D-Speaker |
| pyannote.audio（任意。acoustic_pyannote） | 話者分離 | MIT（モデルは利用条件あり） | https://github.com/pyannote/pyannote-audio |
| Kokoro-82M（samples/ の合成） | 確認用サンプルの合成 | Apache-2.0 | https://huggingface.co/hexgrad/Kokoro-82M |
| misaki | Kokoro の日本語読み | MIT | https://github.com/hexgrad/misaki |
| wav2vec2-lv-60-espeak-cv-ft（facebook、段6 の既定の音素認識） | 音素認識（IPA） | Apache-2.0 | https://huggingface.co/facebook/wav2vec2-lv-60-espeak-cv-ft |
| Hugging Face Transformers / huggingface_hub | wav2vec2 の実行・取得 | Apache-2.0 | https://github.com/huggingface/transformers |
| PyTorch | wav2vec2・allosaurus・Kokoro の実行 | BSD-3-Clause | https://pytorch.org/ |
| allosaurus（任意。phones.provider=allosaurus）と uni2005 モデル | 音素認識（IPA） | GPL-3.0 | https://github.com/xinjli/allosaurus |
| panphon（allosaurus の依存） | 音素の素性 | MIT | https://github.com/dmort27/panphon |
| g2p_en | 参照 run ref_g2p（文字 → ARPAbet） | Apache-2.0 | https://github.com/Kyubyong/g2p |
| CMU Pronouncing Dictionary（g2p_en・NLTK 経由） | 辞書上の読み | BSD-2-Clause | http://www.speech.cs.cmu.edu/cgi-bin/cmudict |
| NLTK（品詞タグ付けデータを含む） | 同形異音語の読み分け | Apache-2.0 | https://www.nltk.org/ |
| misaki[en] / spaCy en_core_web_sm | 英語の合成サンプル（Kokoro の英語の読み） | MIT | https://github.com/hexgrad/misaki , https://spacy.io/ |

## 公開コーパス（段8・9。音声・転写・作った run は `data/`・`sessions/` にだけ置き、リポジトリに入れない）

| コーパス | 使い方 | 利用条件 | 出典 |
|---|---|---|---|
| Speech Accent Archive（Weinberger, S. H., George Mason University） | 日本語話者 44 人と英語母語 44 人の朗読、精密 IPA 転写を参照 run に | CC BY-NC-SA 4.0（非営利・表示・継承） | https://accent.gmu.edu/ , https://osf.io/yh23d/ |
| IDEA: International Dialects of English Archive（japan-1〜17） | 台本の朗読（Comma Gets a Cure / The Rainbow Passage）と精密な音声表記 | サイトの Terms of Use：個人の研究目的のみ。音声の再配布は不可、テキストは短い引用まで | https://www.dialectsarchive.com/ |
| Common Voice 英語（取り込みツールのみ。今回は使っていない） | accent=Japanese のクリップと文 | CC0（取得には Mozilla Data Collective のアカウントが要る） | https://datacollective.mozillafoundation.org/ |

話者を特定しようとしない（コーパスのファイル名の番号以外は使わない）。
