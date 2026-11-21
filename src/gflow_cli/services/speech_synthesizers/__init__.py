"""Speech synthesis provider adapters."""

from gflow_cli.services.speech_synthesizers.edge import EdgeTtsSynthesizer
from gflow_cli.services.speech_synthesizers.gemini import GeminiTtsSynthesizer

__all__ = ["EdgeTtsSynthesizer", "GeminiTtsSynthesizer"]
