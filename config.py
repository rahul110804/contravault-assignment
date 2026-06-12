"""ContraVault configuration."""
import os
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).parent
INTERMEDIATES_DIR = PROJECT_ROOT / "intermediates"
INTERMEDIATES_DIR.mkdir(exist_ok=True)

# LLM
LLM_PROVIDER = "groq"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = "llama-3.1-8b-instant"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

# Embeddings
EMBEDDING_MODEL = "BAAI/bge-m3"

# OCR
OCR_LANGUAGES = "eng+hin"
OCR_DPI = 300
SCANNED_TEXT_THRESHOLD = 50  # chars below this = scanned page

# Retrieval
RETRIEVAL_TOP_K = 5

# Scoring
TECHNICAL_SCORE_RANGE = (0, 100)
