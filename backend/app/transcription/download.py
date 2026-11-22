"""Baixa o modelo Whisper configurado para o cache local: `python -m app.transcription.download`."""

from app.core.config import get_settings
from app.transcription.faster_whisper_provider import load_model

if __name__ == "__main__":
    s = get_settings()
    load_model(s)
    print(f"Modelo '{s.whisper_model}' disponível.")
