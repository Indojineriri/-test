import os

from dotenv import load_dotenv

load_dotenv()

# Claude model. Default to the most capable model; switchable from the UI.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")
AVAILABLE_MODELS = ["claude-opus-4-7", "claude-sonnet-4-6"]

# Google Cloud Storage destination for uploaded decks.
GCS_BUCKET = os.getenv("GCS_BUCKET", "")
GCS_PREFIX = os.getenv("GCS_PREFIX", "meeting-decks")

# Long edge (px) used when rasterising slides for multimodal analysis.
# 1600px keeps token cost reasonable while staying legible.
IMAGE_LONG_EDGE = int(os.getenv("IMAGE_LONG_EDGE", "1600"))
