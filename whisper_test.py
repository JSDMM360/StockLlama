import json
import queue
import sys
import numpy as np
import sounddevice as sd
from vosk import KaldiRecognizer, Model

# Configuration
SAMPLE_RATE = 16000
CHUNK_DURATION = 0.2  # Process audio every 200ms for rapid updates
BLOCK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION)

audio_queue = queue.Queue()


def audio_callback(indata, frames, time, status):
    if status:
        print(status, file=sys.stderr)
    audio_queue.put(indata.copy())


def main():
    print("Loading Vosk model (this may take a moment on first run)...")
    # Automatically downloads the small, highly efficient English model
    model = Model(lang="en-us")
    recognizer = KaldiRecognizer(model, SAMPLE_RATE)

    print("\nInitializing microphone... Speak into your mic!")

    # Vosk expects 16-bit signed integer PCM audio (int16)
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        callback=audio_callback,
        blocksize=BLOCK_SIZE,
        dtype="int16",
    )

    with stream:
        while True:
            try:
                # Get raw audio chunk from the queue
                chunk = audio_queue.get()

                # Convert the chunk back to raw bytes for Vosk
                audio_bytes = bytes(chunk)

                # AcceptWaveform returns True when it detects a complete phrase/silence
                if recognizer.AcceptWaveform(audio_bytes):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "")
                    if text:
                        # Print the final, confirmed sentence
                        print(f"\nFinal: {text}")
                else:
                    # While speaking, fetch partial/live predictions
                    partial_result = json.loads(recognizer.PartialResult())
                    partial_text = partial_result.get("partial", "")
                    if partial_text:
                        # Print updating text on the same line
                        print(f"\rLive: {partial_text}", end="", flush=True)

            except KeyboardInterrupt:
                print("\nStopping transcription.")
                break


if __name__ == "__main__":
    main()