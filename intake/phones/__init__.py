"""音素の観察（どう発音されたか）。provider は STT と同じ位置で VAD 区間ごとに動く。

base.py      Protocol と NoPhones
wav2vec2_espeak.py  facebook/wav2vec2-lv-60-espeak-cv-ft（IPA、torch + transformers）
allo.py      allosaurus（IPA、pip で入る汎用音素認識器）
classes.py   IPA / ARPAbet を粗い音素クラスに丸める（表は classes.yaml）
align.py     参照の音素列と認識の音素列の整列（trace/phones_align.jsonl）
g2p.py       文字 → 辞書上の音素（参照 run ref_g2p 用。観察ではない）
"""
