from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import edge_tts
import miniaudio


class EdgeTtsSynthesizer:
    def __init__(self, *, rate: str, volume: str, pitch: str) -> None:
        self.rate = rate
        self.volume = volume
        self.pitch = pitch

    async def synthesize(self, message: str, voice: str) -> Path:
        mp3_handle = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        mp3_path = Path(mp3_handle.name)
        mp3_handle.close()
        wav_handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        wav_path = Path(wav_handle.name)
        wav_handle.close()

        try:
            communicate = edge_tts.Communicate(
                message,
                voice,
                rate=self.rate,
                volume=self.volume,
                pitch=self.pitch,
            )
            await communicate.save(str(mp3_path))
            decoded = await asyncio.to_thread(
                miniaudio.decode_file,
                str(mp3_path),
                miniaudio.SampleFormat.SIGNED16,
                1,
                8000,
            )
            await asyncio.to_thread(miniaudio.wav_write_file, str(wav_path), decoded)
            return wav_path
        except Exception:
            wav_path.unlink(missing_ok=True)
            raise
        finally:
            mp3_path.unlink(missing_ok=True)
