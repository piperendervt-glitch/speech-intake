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
