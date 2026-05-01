# Piper TTS models on the server

The app’s primary text-to-speech engine is **Piper** (local, CPU). If the `.onnx` voice files are missing, it **falls back to gTTS** (Google over the network), which is slower and less predictable.

Defaults are defined in `app/services/voice_service.py` (`_voice_model_path_for_lang`). You can override paths with environment variables:

| Variable | Default file (under `piper_models/`) |
|----------|--------------------------------------|
| `PIPER_EN_MODEL_PATH` | `en_US-lessac-medium.onnx` |
| `PIPER_UR_MODEL_PATH` | `ur_PK-fasih-medium.onnx` |
| `PIPER_HI_MODEL_PATH` | `hi_IN-rohan-medium.onnx` |

Each voice needs **two** files next to each other:

- `*.onnx` — model weights (large)
- `*.onnx.json` — config (small)

`PiperVoice.load` uses the `.onnx` path; the JSON is picked up automatically when it shares the same base name.

## Quick install (recommended)

From the machine that runs the Flask app:

```bash
cd /path/to/iqbal_ai_stg
bash scripts/download_piper_models.sh
```

This pulls **English**, **Urdu (Pakistan, fasih)**, and **Hindi (India, rohan)** from the official repo [rhasspy/piper-voices](https://huggingface.co/rhasspy/piper-voices) on Hugging Face into `piper_models/`.

Then restart your application workers so voices are loaded fresh.

## Manual download

If you prefer `wget` or a mirror, use the same `resolve/main/...` URLs as in `scripts/download_piper_models.sh`. Example for English:

- `https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx`
- `https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json`

Save them as `piper_models/en_US-lessac-medium.onnx` and `piper_models/en_US-lessac-medium.onnx.json`.

## Docker / production

- **Volume:** mount a host directory (e.g. `/var/lib/iqbal/piper_models`) to `piper_models/` **or** set `PIPER_*_MODEL_PATH` to absolute paths inside the image.
- **CI/CD:** run `download_piper_models.sh` in the image build **or** on first boot; models are ~60–65 MB each (roughly ~190 MB total for all three).
- **Do not** commit `.onnx` files to git; they are large. The repo may ship a download script and small `.json` samples only.

## Verify

1. Call `POST /text-to-speech` or `POST /api/tts` with sample text.
2. On success with Piper, the response is **`audio/wav`** and server logs should not show `Piper model not found` / gTTS fallback warnings for that language.
3. gTTS fallback returns **`audio/mpeg`** (MP3).

## Older voice names

Earlier defaults referenced `ur_PK-urdu-medium` and `hi_IN-hindi-medium`. Those paths no longer match the current `piper-voices` layout on Hugging Face. The app now defaults to **`ur_PK-fasih-medium`** and **`hi_IN-rohan-medium`**. If you already downloaded old files, either re-run the script or point `PIPER_UR_MODEL_PATH` / `PIPER_HI_MODEL_PATH` at your existing `.onnx` files.
