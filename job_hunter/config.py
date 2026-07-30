"""Central configuration loaded from environment / .env file."""

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is optional at runtime
    pass

def get_secret(key: str, default: str = "") -> str:
    """Retrieve secret from Streamlit secrets (for cloud deployments) or env vars."""
    try:
        import streamlit as st
        # st.secrets behaves like a dict and can contain the keys
        if hasattr(st, "secrets") and key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return os.getenv(key, default)


NVIDIA_API_KEY = get_secret("NVIDIA_API_KEY", "")
NVIDIA_MODEL = get_secret("NVIDIA_MODEL", "nvidia/nemotron-3-super-120b-a12b")
NVIDIA_BASE_URL = get_secret("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")


ADZUNA_APP_ID = get_secret("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = get_secret("ADZUNA_APP_KEY", "")
APIFY_API_TOKEN = get_secret("APIFY_API_TOKEN", "")


REQUEST_TIMEOUT = 35
MAX_EVALS_DEFAULT = 40
