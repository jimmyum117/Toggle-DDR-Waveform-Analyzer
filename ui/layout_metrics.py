"""Shared vertical metrics so the signal list lines up with waveform tracks."""

# Extra headroom so marker numbers sit above time-ruler labels.
RULER_HEIGHT = 52
TRACK_HEIGHT = 28
DATA_TRACK_HEIGHT = 36


def track_height_for(signal: str) -> int:
    return DATA_TRACK_HEIGHT if signal == "DATA" else TRACK_HEIGHT
