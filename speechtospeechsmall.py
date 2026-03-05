# mic_s2st.py
# Mic (audio in) -> SeamlessM4T speech-to-speech -> Speaker (audio out)
# Uses a SMALLER model by default: facebook/seamless-m4t-medium
#
# Run:
#   python mic_s2st.py --tgt_lang eng
#   python mic_s2st.py --tgt_lang eng --model_id facebook/seamless-m4t-medium
#   python mic_s2st.py --tgt_lang fra --seconds 5
#
# Optional (fix HF download CAS/Xet issues on Windows PowerShell):
#   $env:HF_HUB_DISABLE_XET="1"
#   $env:HF_HUB_DOWNLOAD_WORKERS="1"

import argparse
import numpy as np
import torch
import torchaudio
import sounddevice as sd

from transformers import (
    AutoProcessor,
    SeamlessM4TForSpeechToSpeech,
    SeamlessM4Tv2ForSpeechToSpeech,
)


def record_audio(duration_s: float, sr: int) -> np.ndarray:
    """Record mono audio from default mic, return float32 numpy array in [-1, 1]."""
    print(f"Recording for {duration_s:.1f}s... (speak now)")
    audio = sd.rec(int(duration_s * sr), samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    return audio.squeeze(axis=1)

def play_audio(audio: np.ndarray, sr: int):
    """Play mono float32 audio."""
    audio = np.asarray(audio, dtype=np.float32).squeeze()
    print("Playing output...")
    sd.play(audio, samplerate=sr)
    sd.wait()

def get_target_sr(processor) -> int:
    # Seamless typically expects 16kHz
    sr = getattr(processor, "sampling_rate", None)
    if sr is None:
        sr = getattr(getattr(processor, "feature_extractor", None), "sampling_rate", None)
    return int(sr or 16000)

def load_model_and_processor(model_id: str, device: torch.device):
    processor = AutoProcessor.from_pretrained(model_id)

    # v2 checkpoints use a different class than v1
    if "v2" in model_id:
        model = SeamlessM4Tv2ForSpeechToSpeech.from_pretrained(model_id).to(device)
    else:
        model = SeamlessM4TForSpeechToSpeech.from_pretrained(model_id).to(device)

    model.eval()
    return processor, model

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tgt_lang", required=True, help='Target language code, e.g. "eng", "fra", "spa"')
    parser.add_argument(
        "--model_id",
        default="facebook/seamless-m4t-medium",  # ✅ smaller than v2-large
        help="Model id. Good CPU default: facebook/seamless-m4t-medium",
    )
    parser.add_argument("--speaker_id", type=int, default=0, help="Speaker id (try 0..199)")
    parser.add_argument("--mic_sr", type=int, default=16000, help="Mic sample rate to record at")
    parser.add_argument("--seconds", type=float, default=4.0, help="Seconds to record per turn")
    args = parser.parse_args()

    device = torch.device("cpu")

    print(f"Loading model: {args.model_id} (CPU)")
    processor, model = load_model_and_processor(args.model_id, device)
    target_sr = get_target_sr(processor)

    print("\nReady. Press ENTER to record, Ctrl+C to quit.")
    while True:
        input("\nPress ENTER to start recording...")
        mic_audio = record_audio(args.seconds, args.mic_sr)

        wav = torch.from_numpy(mic_audio)
        if args.mic_sr != target_sr:
            wav = torchaudio.functional.resample(wav, args.mic_sr, target_sr)

        inputs = processor(audio=wav.numpy(), sampling_rate=target_sr, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}

        print("Translating (speech-to-speech)...")
        with torch.inference_mode():
            out = model.generate(**inputs, tgt_lang=args.tgt_lang, speaker_id=args.speaker_id)[0]
            out_audio = out.cpu().numpy().squeeze().astype(np.float32)

        play_audio(out_audio, target_sr)

if __name__ == "__main__":
    main()


