import argparse
import numpy as np
import torch
import torchaudio
import sounddevice as sd

from transformers import AutoProcessor, SeamlessM4Tv2ForSpeechToSpeech


def record_audio(duration_s: float, sr: int) -> np.ndarray:
    """Record mono audio from default mic, return float32 numpy array in [-1, 1]."""
    print(f"Recording for {duration_s:.1f}s... (speak now)")
    audio = sd.rec(int(duration_s * sr), samplerate=sr, channels=1, dtype="float32")
    sd.wait()
    audio = audio.squeeze(axis=1)  # (samples,)
    return audio


def play_audio(audio: np.ndarray, sr: int):
    """Play mono float32 audio."""
    if audio.ndim != 1:
        audio = audio.squeeze()
    print("Playing output...")
    sd.play(audio.astype(np.float32), samplerate=sr)
    sd.wait()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tgt_lang", required=True, help='Target language code, e.g. "eng", "fra", "spa"')
    parser.add_argument("--model_id", default="facebook/seamless-m4t-v2-large",
                        help="HF model id (try medium/small if available for speed)")
    parser.add_argument("--speaker_id", type=int, default=0, help="Speaker id (try 0..199)")
    parser.add_argument("--mic_sr", type=int, default=16000, help="Mic sample rate to record at")
    parser.add_argument("--seconds", type=float, default=4.0, help="Seconds to record per turn")
    args = parser.parse_args()

    device = torch.device("cpu")

    print(f"Loading model: {args.model_id} (CPU)")
    processor = AutoProcessor.from_pretrained(args.model_id)
    model = SeamlessM4Tv2ForSpeechToSpeech.from_pretrained(args.model_id).to(device)
    model.eval()

    # Seamless expects a particular SR (usually 16k)
    target_sr = getattr(processor, "sampling_rate", None)
    if target_sr is None:
        target_sr = getattr(getattr(processor, "feature_extractor", None), "sampling_rate", 16000)

    print("\nReady. Press ENTER to record, Ctrl+C to quit.")
    while True:
        input("\nPress ENTER to start recording...")
        mic_audio = record_audio(args.seconds, args.mic_sr)

        # Resample to model SR if needed
        wav = torch.from_numpy(mic_audio)
        if args.mic_sr != target_sr:
            wav = torchaudio.functional.resample(wav, args.mic_sr, target_sr)

        # Processor expects numpy
        inputs = processor(audio=wav.numpy(), sampling_rate=target_sr, return_tensors="pt")
        inputs = {k: v.to(device) for k, v in inputs.items()}

        print("Translating (speech-to-speech)...")
        with torch.inference_mode():
            out = model.generate(**inputs, tgt_lang=args.tgt_lang, speaker_id=args.speaker_id)[0]
            out_audio = out.cpu().numpy().squeeze().astype(np.float32)

        play_audio(out_audio, target_sr)


if __name__ == "__main__":
    main()