import gc
import hashlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from fpdf import FPDF
from openai import OpenAI
from pydub import AudioSegment

import librosa

APP_TITLE = "Optimizacion del Analisis de Coyuntura Politica con IA"
APP_DESCRIPTION = "Artefacto DSRM para el analisis estrategico de discursos de poder"
APP_SUBTITLE = "Flujo: audio -> M4A/AAC optimizado -> Whisper -> perfil de audio -> filtro -> analisis -> exportacion"

BASE_DIR = Path(__file__).resolve().parent
PROMPTS_DIR = BASE_DIR / "src/system_prompts"
PROJECT_DOCS_DIR = BASE_DIR / "src/project_docs"


def _read_secret_or_env(*keys: str) -> str | None:
    """Lee primero Streamlit secrets y luego variables de entorno."""
    for key in keys:
        try:
            if key in st.secrets:
                value = st.secrets.get(key)
                if value is not None and str(value).strip() != "":
                    return str(value).strip()
        except Exception:
            pass
        value = os.getenv(key)
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return None



def _read_float_setting(key: str, default: float) -> float:
    raw = _read_secret_or_env(key)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _read_int_setting(key: str, default: int) -> int:
    raw = _read_secret_or_env(key)
    if raw is None:
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


st.set_page_config(
    page_title=APP_TITLE,
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Configuracion. 
OPENAI_TRANSCRIPTION_MODEL = _read_secret_or_env("OPENAI_TRANSCRIPTION_MODEL") or "whisper-1"
OPENAI_ANALYSIS_MODEL = _read_secret_or_env("OPENAI_ANALYSIS_MODEL") or "gpt-5.6-luna"
OPENAI_AUDIO_FILE_LIMIT_MB = _read_float_setting("OPENAI_AUDIO_FILE_LIMIT_MB", 25.0)
OPENAI_AUDIO_FILE_LIMIT_BYTES = int(OPENAI_AUDIO_FILE_LIMIT_MB * 1024 * 1024)
OPENAI_API_TIMEOUT_S = _read_float_setting("OPENAI_API_TIMEOUT_S", 180.0)

PAUSE_DRAMATIC_THRESHOLD_S = _read_float_setting("PAUSE_DRAMATIC_THRESHOLD_S", 1.5)
ELONGATED_WORD_THRESHOLD_S = _read_float_setting("ELONGATED_WORD_THRESHOLD_S", 0.6)
LOW_CONFIDENCE_LOGPROB_THRESHOLD = _read_float_setting("LOW_CONFIDENCE_LOGPROB_THRESHOLD", -1.0)
HIGH_COMPRESSION_RATIO_THRESHOLD = _read_float_setting("HIGH_COMPRESSION_RATIO_THRESHOLD", 2.4)
HIGH_NO_SPEECH_PROB_THRESHOLD = _read_float_setting("HIGH_NO_SPEECH_PROB_THRESHOLD", 0.6)

# Precios de referencia configurables. Valores oficiales observados el 2026-07-22.
PRICING_REFERENCE_DATE = _read_secret_or_env("PRICING_REFERENCE_DATE") or "2026-07-22"
WHISPER_PRICE_PER_MINUTE_USD = _read_float_setting("WHISPER_PRICE_PER_MINUTE_USD", 0.006)
GPT_LONG_CONTEXT_THRESHOLD_TOKENS = _read_int_setting("GPT_LONG_CONTEXT_THRESHOLD_TOKENS", 272_000)
GPT_SHORT_INPUT_PRICE_PER_1M = _read_float_setting("GPT_SHORT_INPUT_PRICE_PER_1M", 1.00)
GPT_SHORT_CACHED_INPUT_PRICE_PER_1M = _read_float_setting("GPT_SHORT_CACHED_INPUT_PRICE_PER_1M", 0.10)
GPT_SHORT_CACHE_WRITE_PRICE_PER_1M = _read_float_setting("GPT_SHORT_CACHE_WRITE_PRICE_PER_1M", 1.25)
GPT_SHORT_OUTPUT_PRICE_PER_1M = _read_float_setting("GPT_SHORT_OUTPUT_PRICE_PER_1M", 6.00)
GPT_LONG_INPUT_PRICE_PER_1M = _read_float_setting("GPT_LONG_INPUT_PRICE_PER_1M", 2.00)
GPT_LONG_CACHED_INPUT_PRICE_PER_1M = _read_float_setting("GPT_LONG_CACHED_INPUT_PRICE_PER_1M", 0.20)
GPT_LONG_CACHE_WRITE_PRICE_PER_1M = _read_float_setting("GPT_LONG_CACHE_WRITE_PRICE_PER_1M", 2.50)
GPT_LONG_OUTPUT_PRICE_PER_1M = _read_float_setting("GPT_LONG_OUTPUT_PRICE_PER_1M", 9.00)

# Preprocesamiento de audio
# M4A/AAC mono a 16 kHz y 64 kbps para la carga a OpenAI, mas un WAV PCM
# separado para el analisis local con Librosa. El WAV ya no se envia a la API.
OPENAI_AUDIO_OUTPUT_FORMAT = "m4a"
OPENAI_AUDIO_CODEC = "aac"
OPENAI_AUDIO_BITRATE_KBPS = _read_int_setting("OPENAI_AUDIO_BITRATE_KBPS", 64)
OPENAI_AUDIO_ESTIMATE_OVERHEAD_FACTOR = _read_float_setting(
    "OPENAI_AUDIO_ESTIMATE_OVERHEAD_FACTOR",
    1.07,
)
AUDIO_SAMPLE_RATE_HZ = 16_000
AUDIO_CHANNELS = 1
WAV_SAMPLE_RATE_HZ = AUDIO_SAMPLE_RATE_HZ
WAV_CHANNELS = AUDIO_CHANNELS
WAV_SAMPLE_WIDTH_BYTES = 2
WAV_HEADER_ESTIMATE_BYTES = 44
M4A_CONTAINER_ESTIMATE_BYTES = 4_096
THEORETICAL_MAX_API_AUDIO_DURATION_S = max(
    0.0,
    (OPENAI_AUDIO_FILE_LIMIT_BYTES - M4A_CONTAINER_ESTIMATE_BYTES) * 8
    / (
        OPENAI_AUDIO_BITRATE_KBPS
        * 1_000
        * OPENAI_AUDIO_ESTIMATE_OVERHEAD_FACTOR
    ),
)

st.markdown(
    """
    <style>
    .main .block-container {max-width: 1180px; padding-top: 1.35rem; padding-bottom: 3rem;}
    .stButton > button, .stDownloadButton > button {border-radius: 10px; font-weight: 600;}
    .stTextInput input, .stTextArea textarea {border-radius: 10px;}
    .stTabs [data-baseweb="tab-list"] {gap: 0.5rem;}
    .stTabs [data-baseweb="tab"] {padding: 0.65rem 0.9rem; border-radius: 10px; height: auto;}
    [data-testid="stMetric"] {background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 12px; padding: 0.9rem;}
    [data-testid="stMetric"] * {color: #0f172a !important;}
    [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] *,
    [data-testid="stMetricValue"], [data-testid="stMetricValue"] *,
    [data-testid="stMetricDelta"], [data-testid="stMetricDelta"] * {color: #0f172a !important;}
    .app-hero {
        background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
        border: 1px solid #e2e8f0;
        border-radius: 16px;
        padding: 1.2rem 1.35rem;
        margin-bottom: 0.9rem;
    }
    .app-hero h1 {font-size: 2rem; margin: 0 0 0.3rem 0; color: #0f172a;}
    .app-hero p {margin: 0.15rem 0; color: #475569;}
    .app-note {
        background: #f8fafc;
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        padding: 0.9rem 1rem;
        color: #334155;
        margin-bottom: 0.9rem;
    }
    .soft-card {
        background: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        padding: 1rem 1.1rem;
        margin-bottom: 0.9rem;
    }
    .soft-card h3, .soft-card h4 {margin-top: 0; color: #0f172a;}
    .soft-muted {color: #64748b; font-size: 0.92rem;}
    .badge-good {
        display: inline-block;
        background: #dcfce7;
        color: #166534;
        border: 1px solid #86efac;
        border-radius: 999px;
        padding: 0.2rem 0.65rem;
        font-size: 0.82rem;
        font-weight: 600;
    }
    .badge-bad {
        display: inline-block;
        background: #fee2e2;
        color: #991b1b;
        border: 1px solid #fca5a5;
        border-radius: 999px;
        padding: 0.2rem 0.65rem;
        font-size: 0.82rem;
        font-weight: 600;
    }
    .mini-kpi {
        border: 1px solid #e2e8f0;
        border-radius: 12px;
        background: #ffffff;
        padding: 0.8rem 0.9rem;
        height: 100%;
    }
    .mini-kpi .label {color: #64748b; font-size: 0.82rem; margin-bottom: 0.2rem;}
    .mini-kpi .value {color: #0f172a; font-size: 1.15rem; font-weight: 700;}
    .result-kpi {
        border: 1px solid #e2e8f0;
        border-radius: 14px;
        background: linear-gradient(180deg, #ffffff 0%, #f8fafc 100%);
        padding: 0.9rem 1rem;
        min-height: 86px;
        box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }
    .result-kpi .label {color: #475569 !important; font-size: 0.82rem; font-weight: 700; margin-bottom: 0.25rem;}
    .result-kpi .value {color: #0f172a !important; font-size: 1.65rem; line-height: 1.1; font-weight: 800;}
    .result-kpi .note {color: #64748b !important; font-size: 0.78rem; margin-top: 0.35rem;}
    .project-chip {
        display: inline-block;
        border: 1px solid #cbd5e1;
        border-radius: 999px;
        padding: 0.15rem 0.6rem;
        margin-right: 0.35rem;
        margin-bottom: 0.4rem;
        font-size: 0.82rem;
        color: #334155;
        background: #ffffff;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@dataclass(frozen=True)
class PromptProfile:
    profile_id: str
    name: str
    description: str
    is_default: bool
    relevance_prompt: str
    analysis_prompt: str
    source_path: Path


@dataclass(frozen=True)
class ProjectDoc:
    doc_id: str
    title: str
    description: str
    order: int
    is_default: bool
    source_path: Path
    body: str


STATE_DEFAULTS: dict[str, Any] = {
    "transcript": None,
    "transcription_payload": None,
    "relevance": None,
    "analysis": None,
    "audio_profile": None,
    "segment_rows": None,
    "segment_export_rows": None,
    "segment_csv_bytes": None,
    "audio_brief_text": None,
    "audio_llm_payload": None,
    "pdf_bytes": None,
    "processing_time_s": None,
    "audio_duration_s": None,
    "word_count": None,
    "audio_hash": None,
    "raw_relevance_response": None,
    "raw_analysis_response": None,
    "last_prompt_profile": None,
    "last_llm_engine": None,
    "last_transcription_engine": None,
    "selected_project_doc_id": None,
    "audio_file_name": None,
    "audio_original_size_bytes": None,
    "audio_api_size_bytes": None,
    "audio_analysis_wav_size_bytes": None,
    "audio_api_format": None,
    "audio_preview": None,
    "whisper_telemetry": None,
    "relevance_telemetry": None,
    "analysis_telemetry": None,
    "pipeline_stage_timings": None,
    "technical_summary": None,
    "technical_summary_text": None,
    "estimated_total_cost_usd": None,
    "last_run_status": None,
    "last_error_message": None,
    "run_started_at": None,
    "run_finished_at": None,
}


def init_session_state() -> None:
    for key, value in STATE_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _parse_scalar(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in {"true", "yes", "si", "sí"}:
        return True
    if lowered in {"false", "no"}:
        return False
    try:
        return int(value.strip())
    except Exception:
        return value.strip()


def _extract_front_matter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not match:
        return {}, text
    raw_meta = match.group(1)
    body = match.group(2)
    meta: dict[str, Any] = {}
    for line in raw_meta.splitlines():
        line = line.strip()
        if not line or ":" not in line or line.startswith("#"):
            continue
        key, raw_value = line.split(":", 1)
        meta[key.strip()] = _parse_scalar(raw_value)
    return meta, body


def _extract_prompt_sections(body: str) -> tuple[str, str]:
    pattern = re.compile(r"(?ms)^##\s*RELEVANCE_PROMPT\s*$\n(.*?)^##\s*ANALYSIS_PROMPT\s*$\n(.*)$")
    match = pattern.search(body.strip())
    if not match:
        raise ValueError("El archivo no contiene las secciones '## RELEVANCE_PROMPT' y '## ANALYSIS_PROMPT'.")
    relevance_prompt = match.group(1).strip()
    analysis_prompt = match.group(2).strip()
    if not relevance_prompt or not analysis_prompt:
        raise ValueError("Las secciones de prompt no pueden estar vacias.")
    return relevance_prompt, analysis_prompt


def load_prompt_profiles(directory: Path) -> tuple[list[PromptProfile], list[str]]:
    profiles: list[PromptProfile] = []
    diagnostics: list[str] = []
    if not directory.exists():
        diagnostics.append(f"No existe el directorio de prompts: {directory}")
        return profiles, diagnostics

    prompt_files = [path for path in sorted(directory.glob("*.md")) if path.stem.lower() != "readme"]
    if not prompt_files:
        diagnostics.append(f"No se encontraron archivos .md de prompts en {directory}")

    for path in prompt_files:
        try:
            raw_text = path.read_text(encoding="utf-8")
            meta, body = _extract_front_matter(raw_text)
            if not meta:
                raise ValueError("falta front matter YAML delimitado por ---")
            relevance_prompt, analysis_prompt = _extract_prompt_sections(body)
            profiles.append(
                PromptProfile(
                    profile_id=str(meta.get("id") or path.stem),
                    name=str(meta.get("name") or path.stem),
                    description=str(meta.get("description") or "Sin descripcion."),
                    is_default=bool(meta.get("default", False)),
                    relevance_prompt=relevance_prompt,
                    analysis_prompt=analysis_prompt,
                    source_path=path,
                )
            )
        except Exception as exc:
            diagnostics.append(f"{path.name}: {exc}")

    return profiles, diagnostics


def load_project_docs(directory: Path) -> tuple[list[ProjectDoc], list[str]]:
    docs: list[ProjectDoc] = []
    diagnostics: list[str] = []
    if not directory.exists():
        diagnostics.append(f"No existe el directorio de documentos del proyecto: {directory}")
        return docs, diagnostics

    for path in sorted(directory.glob("*.md")):
        if path.stem.lower() == "readme":
            continue
        try:
            raw_text = path.read_text(encoding="utf-8")
            meta, body = _extract_front_matter(raw_text)
            docs.append(
                ProjectDoc(
                    doc_id=str(meta.get("id") or path.stem),
                    title=str(meta.get("title") or meta.get("name") or path.stem),
                    description=str(meta.get("description") or "Documento del proyecto."),
                    order=int(meta.get("order", 999)),
                    is_default=bool(meta.get("default", False)),
                    source_path=path,
                    body=body.strip(),
                )
            )
        except Exception as exc:
            diagnostics.append(f"{path.name}: {exc}")
    docs.sort(key=lambda item: (item.order, item.title.lower()))
    return docs, diagnostics


def get_openai_client() -> OpenAI:
    api_key = _read_secret_or_env("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Falta OPENAI_API_KEY. Configurala en .streamlit/secrets.toml o como variable de entorno.")
    return OpenAI(api_key=api_key, timeout=OPENAI_API_TIMEOUT_S)


def _package_version(package_name: str) -> str:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return "no detectada"
    except Exception:
        return "desconocida"


def _format_bytes(size_bytes: int | float | None) -> str:
    if size_bytes is None:
        return "N/A"
    value = float(size_bytes)
    units = ["B", "KB", "MB", "GB"]
    unit = units[0]
    for unit in units:
        if abs(value) < 1024.0 or unit == units[-1]:
            break
        value /= 1024.0
    decimals = 0 if unit == "B" else 2
    return f"{value:.{decimals}f} {unit}"


def _format_usd(value: float | None, decimals: int = 6) -> str:
    if value is None:
        return "N/A"
    return f"US${float(value):.{decimals}f}"


def _audio_format_from_name(file_name: str | None) -> str | None:
    if not file_name:
        return None
    suffix = Path(file_name).suffix.lower().replace(".", "")
    if not suffix:
        return None
    if suffix == "m4a":
        return "mp4"
    if suffix == "oga":
        return "ogg"
    return suffix


@st.cache_data(show_spinner=False)
def inspect_audio_input(audio_bytes: bytes, file_name: str | None = None) -> dict[str, Any]:
    """Inspeccion local previa, sin llamadas facturables a OpenAI."""
    format_hint = _audio_format_from_name(file_name)
    try:
        if format_hint:
            audio = AudioSegment.from_file(io.BytesIO(audio_bytes), format=format_hint)
        else:
            audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
    except Exception as exc:
        raise RuntimeError(
            "No fue posible inspeccionar el audio. Verifica el formato del archivo y que ffmpeg este instalado."
        ) from exc

    duration_s = len(audio) / 1000.0
    estimated_api_audio_bytes = int(
        M4A_CONTAINER_ESTIMATE_BYTES
        + duration_s
        * (OPENAI_AUDIO_BITRATE_KBPS * 1_000 / 8)
        * OPENAI_AUDIO_ESTIMATE_OVERHEAD_FACTOR
    )
    estimated_analysis_wav_bytes = int(
        WAV_HEADER_ESTIMATE_BYTES
        + duration_s * WAV_SAMPLE_RATE_HZ * WAV_CHANNELS * WAV_SAMPLE_WIDTH_BYTES
    )
    return {
        "file_name": file_name or "audio_sin_nombre",
        "format": Path(file_name).suffix.lower().lstrip(".") if file_name else "desconocido",
        "original_size_bytes": len(audio_bytes),
        "duration_s": round(duration_s, 2),
        "estimated_api_audio_size_bytes": estimated_api_audio_bytes,
        "estimated_analysis_wav_size_bytes": estimated_analysis_wav_bytes,
        "estimated_api_audio_exceeds_limit": estimated_api_audio_bytes >= OPENAI_AUDIO_FILE_LIMIT_BYTES,
        "api_file_limit_bytes": OPENAI_AUDIO_FILE_LIMIT_BYTES,
        "api_audio_format": OPENAI_AUDIO_OUTPUT_FORMAT,
        "api_audio_codec": OPENAI_AUDIO_CODEC,
        "api_audio_bitrate_kbps": OPENAI_AUDIO_BITRATE_KBPS,
        "api_audio_sample_rate_hz": AUDIO_SAMPLE_RATE_HZ,
        "api_audio_channels": AUDIO_CHANNELS,
    }


def build_preflight_report(
    prompt_profiles: list[PromptProfile],
    prompt_diagnostics: list[str],
    project_doc_diagnostics: list[str],
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    api_key_ok = bool(_read_secret_or_env("OPENAI_API_KEY"))
    checks.append(
        {
            "name": "OPENAI_API_KEY",
            "ok": api_key_ok,
            "critical": True,
            "detail": "Presencia detectada en secrets o entorno (no se valida contra la API hasta ejecutar)." if api_key_ok else "No configurada.",
        }
    )

    ffmpeg_path = shutil.which("ffmpeg")
    checks.append(
        {
            "name": "ffmpeg",
            "ok": bool(ffmpeg_path),
            "critical": True,
            "detail": ffmpeg_path or "No encontrado en PATH.",
        }
    )

    checks.append(
        {
            "name": "librosa",
            "ok": True,
            "critical": True,
            "detail": f"Version {_package_version('librosa')}",
        }
    )

    checks.append(
        {
            "name": "Perfiles de prompt",
            "ok": bool(prompt_profiles),
            "critical": True,
            "detail": f"{len(prompt_profiles)} perfil(es) valido(s).",
        }
    )

    checks.append(
        {
            "name": "Directorio de documentos",
            "ok": PROJECT_DOCS_DIR.exists(),
            "critical": False,
            "detail": str(PROJECT_DOCS_DIR),
        }
    )

    critical_errors = [item for item in checks if item["critical"] and not item["ok"]]
    return {
        "ready": not critical_errors,
        "checks": checks,
        "prompt_diagnostics": prompt_diagnostics,
        "project_doc_diagnostics": project_doc_diagnostics,
        "versions": {
            "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
            "streamlit": _package_version("streamlit"),
            "openai": _package_version("openai"),
            "librosa": _package_version("librosa"),
            "pydub": _package_version("pydub"),
            "ffmpeg_path": ffmpeg_path,
        },
    }


def prepare_audio_files(
    audio_bytes: bytes,
    file_name: str | None = None,
) -> dict[str, Any]:
    """Genera dos artefactos temporales desde el audio original.

    1. M4A/AAC mono, 16 kHz y bitrate configurable: archivo enviado a OpenAI.
    2. WAV PCM s16le mono, 16 kHz: archivo local usado por Librosa.

    Esta separacion evita enviar el WAV sin compresion a la API y conserva un
    insumo PCM estable para el analisis acustico local.
    """
    input_suffix = Path(file_name or "audio.bin").suffix.lower() or ".audio"
    input_file = tempfile.NamedTemporaryFile(suffix=input_suffix, delete=False)
    input_path = input_file.name
    input_file.write(audio_bytes)
    input_file.close()

    api_fd, api_audio_path = tempfile.mkstemp(suffix="_openai.m4a")
    os.close(api_fd)
    wav_fd, analysis_wav_path = tempfile.mkstemp(suffix="_analysis.wav")
    os.close(wav_fd)

    created_paths = [input_path, api_audio_path, analysis_wav_path]
    try:
        try:
            audio = AudioSegment.from_file(input_path)
            duration_s = round(len(audio) / 1000.0, 2)
        except Exception as exc:
            raise RuntimeError(
                "No fue posible leer la duracion del audio antes de convertirlo."
            ) from exc

        api_command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            input_path,
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            OPENAI_AUDIO_CODEC,
            "-ac",
            str(AUDIO_CHANNELS),
            "-ar",
            str(AUDIO_SAMPLE_RATE_HZ),
            "-b:a",
            f"{OPENAI_AUDIO_BITRATE_KBPS}k",
            api_audio_path,
        ]
        wav_command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            input_path,
            "-vn",
            "-map",
            "0:a:0",
            "-c:a",
            "pcm_s16le",
            "-ac",
            str(WAV_CHANNELS),
            "-ar",
            str(WAV_SAMPLE_RATE_HZ),
            analysis_wav_path,
        ]

        subprocess.run(
            api_command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        subprocess.run(
            wav_command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        return {
            "input_path": input_path,
            "api_audio_path": api_audio_path,
            "analysis_wav_path": analysis_wav_path,
            "duration_s": duration_s,
            "api_audio_size_bytes": os.path.getsize(api_audio_path),
            "analysis_wav_size_bytes": os.path.getsize(analysis_wav_path),
        }
    except FileNotFoundError as exc:
        for path in created_paths:
            if path and os.path.exists(path):
                os.remove(path)
        raise RuntimeError(
            "No se encontro FFmpeg. Verifica que este instalado y disponible en PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        for path in created_paths:
            if path and os.path.exists(path):
                os.remove(path)
        detail = (exc.stderr or "").strip()
        raise RuntimeError(
            "FFmpeg fallo durante la optimizacion del audio. "
            + (f"Detalle: {detail}" if detail else "")
        ) from exc
    except Exception:
        for path in created_paths:
            if path and os.path.exists(path):
                os.remove(path)
        raise


def _coerce_to_dict(response: Any) -> dict[str, Any]:
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        return response.model_dump()
    if hasattr(response, "to_dict"):
        return response.to_dict()
    if hasattr(response, "__dict__"):
        raw = dict(response.__dict__)
        if raw:
            return raw
    if isinstance(response, str):
        return {"text": response}
    raise TypeError("No fue posible convertir la respuesta del motor de transcripcion a dict.")


def transcribe_with_openai_api(
    api_audio_path: str,
    initial_prompt: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    client = get_openai_client()
    started = time.perf_counter()
    with open(api_audio_path, "rb") as audio_file:
        response = client.audio.transcriptions.create(
            model=OPENAI_TRANSCRIPTION_MODEL,
            file=audio_file,
            prompt=initial_prompt or None,
            response_format="verbose_json",
            timestamp_granularities=["segment", "word"],
        )
    elapsed_s = round(time.perf_counter() - started, 3)
    payload = _coerce_to_dict(response)
    telemetry = {
        "model_requested": OPENAI_TRANSCRIPTION_MODEL,
        "model_returned": _as_text(payload.get("model"), OPENAI_TRANSCRIPTION_MODEL),
        "request_id": getattr(response, "_request_id", None),
        "elapsed_s": elapsed_s,
        "initial_prompt_characters": len(initial_prompt or ""),
        "upload_format": OPENAI_AUDIO_OUTPUT_FORMAT,
        "upload_codec": OPENAI_AUDIO_CODEC,
        "upload_bitrate_kbps": OPENAI_AUDIO_BITRATE_KBPS,
        "upload_sample_rate_hz": AUDIO_SAMPLE_RATE_HZ,
        "upload_channels": AUDIO_CHANNELS,
        "usage_returned_by_openai": payload.get("usage"),
    }
    return payload, telemetry


def _extract_transcript_text(payload: dict[str, Any]) -> str:
    text = str(payload.get("text", "")).strip()
    if text:
        return text
    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    merged = " ".join(str(seg.get("text", "")).strip() for seg in segments if str(seg.get("text", "")).strip())
    return merged.strip()


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _as_text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    return str(value).strip()


def _as_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "si", "sí"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return fallback


def _as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return fallback


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        items = value
    else:
        items = [value]
    cleaned: list[str] = []
    for item in items:
        text = _as_text(item)
        if text:
            cleaned.append(text)
    return cleaned


def _seconds_to_label(seconds: float | None) -> str:
    if seconds is None:
        return "N/A"
    total = max(0, int(round(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _safe_mean(values: list[float | None]) -> float | None:
    valid = [float(v) for v in values if v is not None and not np.isnan(v)]
    if not valid:
        return None
    return float(np.mean(valid))


def _safe_std(values: list[float | None]) -> float | None:
    valid = [float(v) for v in values if v is not None and not np.isnan(v)]
    if not valid:
        return None
    return float(np.std(valid))


def _safe_median(values: list[float | None]) -> float | None:
    valid = [float(v) for v in values if v is not None and not np.isnan(v)]
    if not valid:
        return None
    return float(np.median(valid))


def _zscore_list(values: list[float | None]) -> list[float | None]:
    valid = [float(v) for v in values if v is not None and not np.isnan(v)]
    if len(valid) < 2:
        return [0.0 if v is not None else None for v in values]
    mean = float(np.mean(valid))
    std = float(np.std(valid))
    if std == 0:
        return [0.0 if v is not None else None for v in values]
    output: list[float | None] = []
    for value in values:
        if value is None or np.isnan(value):
            output.append(None)
        else:
            output.append((float(value) - mean) / std)
    return output


def _normalize_to_0_100(values: list[float | None]) -> list[float | None]:
    valid = [float(v) for v in values if v is not None and not np.isnan(v)]
    if not valid:
        return [None for _ in values]
    vmin = float(min(valid))
    vmax = float(max(valid))
    if vmax == vmin:
        return [50.0 if v is not None else None for v in values]
    scaled: list[float | None] = []
    for value in values:
        if value is None or np.isnan(value):
            scaled.append(None)
        else:
            scaled.append(round((float(value) - vmin) * 100.0 / (vmax - vmin), 2))
    return scaled


def normalize_segment_rows(raw_segments: list[dict[str, Any]], transcript_text: str) -> list[dict[str, Any]]:
    if not raw_segments:
        fallback_text = transcript_text.strip()
        if not fallback_text:
            return []
        return [
            {
                "segment_id": 0,
                "start_s": 0.0,
                "end_s": None,
                "duration_s": None,
                "time_label": "00:00",
                "text": fallback_text,
                "word_count": len(fallback_text.split()),
                "token_count": None,
                "pause_before_s": 0.0,
                "avg_logprob": None,
                "no_speech_prob": None,
                "compression_ratio": None,
                "temperature": None,
                "speech_rate_wpm": None,
                "elongated_words": [],
                "low_prob_words": [],
                "rms_mean": None,
                "pitch_median_hz": None,
                "pitch_std_hz": None,
                "zcr_mean": None,
                "intensity_index": None,
                "quality_flags": [],
                "word_details": [],
            }
        ]

    rows: list[dict[str, Any]] = []
    previous_end = 0.0
    for idx, segment in enumerate(raw_segments):
        start_s = _as_float(segment.get("start")) or 0.0
        end_raw = _as_float(segment.get("end"))
        end_s = end_raw if end_raw is not None else start_s
        duration_s = max(0.0, end_s - start_s) if end_raw is not None else None
        text = _as_text(segment.get("text"))
        word_details: list[dict[str, Any]] = []
        raw_words = segment.get("words") if isinstance(segment.get("words"), list) else []
        for word in raw_words:
            start_word = _as_float(word.get("start"))
            end_word = _as_float(word.get("end"))
            duration_word = None
            if start_word is not None and end_word is not None:
                duration_word = round(max(0.0, end_word - start_word), 3)
            word_details.append(
                {
                    "word": _as_text(word.get("word")).strip(),
                    "start_s": start_word,
                    "end_s": end_word,
                    "duration_s": duration_word,
                    "probability": _as_float(word.get("probability")),
                }
            )

        usable_words = [item["word"] for item in word_details if item["word"]]
        word_count = len(usable_words) if usable_words else len(text.split())
        token_count = len(segment.get("tokens")) if isinstance(segment.get("tokens"), list) else None
        pause_before_s = max(0.0, start_s - previous_end)
        speech_rate_wpm = round(word_count * 60.0 / duration_s, 2) if duration_s and duration_s > 0 else None

        elongated_words = sorted(
            {
                item["word"]
                for item in word_details
                if item["duration_s"] is not None and item["duration_s"] >= ELONGATED_WORD_THRESHOLD_S
            }
        )
        low_prob_words = sorted(
            {
                item["word"]
                for item in word_details
                if item["probability"] is not None and item["probability"] < 0.50
            }
        )

        avg_logprob = _as_float(segment.get("avg_logprob"))
        no_speech_prob = _as_float(segment.get("no_speech_prob"))
        compression_ratio = _as_float(segment.get("compression_ratio"))
        temperature = _as_float(segment.get("temperature"))

        quality_flags: list[str] = []
        if avg_logprob is not None and avg_logprob < LOW_CONFIDENCE_LOGPROB_THRESHOLD:
            quality_flags.append("logprob_baja")
        if compression_ratio is not None and compression_ratio > HIGH_COMPRESSION_RATIO_THRESHOLD:
            quality_flags.append("compresion_alta")
        if no_speech_prob is not None and no_speech_prob > HIGH_NO_SPEECH_PROB_THRESHOLD:
            quality_flags.append("ruido_o_silencio_alto")
        if pause_before_s >= PAUSE_DRAMATIC_THRESHOLD_S:
            quality_flags.append("pausa_dramatica")
        if elongated_words:
            quality_flags.append("enfasis_por_duracion")

        rows.append(
            {
                "segment_id": _as_int(segment.get("id"), fallback=idx),
                "start_s": round(start_s, 2),
                "end_s": round(end_s, 2) if end_raw is not None else None,
                "duration_s": round(duration_s, 2) if duration_s is not None else None,
                "time_label": _seconds_to_label(start_s),
                "text": text,
                "word_count": word_count,
                "token_count": token_count,
                "pause_before_s": round(pause_before_s, 2),
                "avg_logprob": round(avg_logprob, 4) if avg_logprob is not None else None,
                "no_speech_prob": round(no_speech_prob, 4) if no_speech_prob is not None else None,
                "compression_ratio": round(compression_ratio, 3) if compression_ratio is not None else None,
                "temperature": round(temperature, 3) if temperature is not None else None,
                "speech_rate_wpm": speech_rate_wpm,
                "elongated_words": elongated_words,
                "low_prob_words": low_prob_words,
                "rms_mean": None,
                "pitch_median_hz": None,
                "pitch_std_hz": None,
                "zcr_mean": None,
                "intensity_index": None,
                "quality_flags": quality_flags,
                "word_details": word_details,
            }
        )
        previous_end = end_s

    return rows


def _slice_feature_by_time(times: np.ndarray, values: np.ndarray, start_s: float, end_s: float | None) -> np.ndarray:
    if end_s is None:
        mask = times >= start_s
    else:
        mask = (times >= start_s) & (times < max(start_s + 1e-6, end_s))
    sliced = values[mask]
    if sliced.size == 0 and values.size:
        nearest_index = int(np.argmin(np.abs(times - start_s)))
        return values[max(0, nearest_index - 1) : nearest_index + 1]
    return sliced


def enrich_segments_with_audio_features(wav_path: str, segment_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    profile: dict[str, Any] = {
        "available": False,
        "method": "Whisper segment metadata only",
        "summary": {},
        "salient_moments": [],
        "dramatic_pauses": [],
        "quality_warnings": [],
        "cautions": [
            "El perfil de audio es exploratorio y no sustituye la interpretacion politologica.",
            "No debe usarse como detector automatico de emociones ni para comparaciones ingenuas entre oradores.",
        ],
    }

    if not segment_rows:
        return segment_rows, profile


    try:
        y, sr = librosa.load(wav_path, sr=16000, mono=True)
        frame_length = 2048
        hop_length = 512

        rms = librosa.feature.rms(y=y, frame_length=frame_length, hop_length=hop_length)[0]
        zcr = librosa.feature.zero_crossing_rate(y, frame_length=frame_length, hop_length=hop_length)[0]
        times = librosa.times_like(rms, sr=sr, hop_length=hop_length)

        f0_values = None
        voiced_ratio = None
        pitch_available = False
        try:
            f0_values, voiced_flag, _ = librosa.pyin(
                y,
                fmin=librosa.note_to_hz("C2"),
                fmax=librosa.note_to_hz("C7"),
                sr=sr,
                frame_length=frame_length,
                hop_length=hop_length,
            )
            voiced_ratio = float(np.mean(~np.isnan(f0_values))) if f0_values is not None else None
            pitch_available = f0_values is not None and np.isfinite(f0_values).any()
        except Exception:
            try:
                f0_values = librosa.yin(
                    y,
                    fmin=librosa.note_to_hz("C2"),
                    fmax=librosa.note_to_hz("C7"),
                    sr=sr,
                    frame_length=frame_length,
                    hop_length=hop_length,
                )
                voiced_ratio = float(np.mean(np.isfinite(f0_values))) if f0_values is not None else None
                pitch_available = f0_values is not None and np.isfinite(f0_values).any()
            except Exception:
                f0_values = None
                voiced_ratio = None
                pitch_available = False

        for row in segment_rows:
            start_s = float(row["start_s"] or 0.0)
            end_s = row["end_s"] if row["end_s"] is not None else None

            rms_slice = _slice_feature_by_time(times, rms, start_s, end_s)
            row["rms_mean"] = round(float(np.nanmean(rms_slice)), 6) if rms_slice.size else None

            zcr_slice = _slice_feature_by_time(times, zcr, start_s, end_s)
            row["zcr_mean"] = round(float(np.nanmean(zcr_slice)), 6) if zcr_slice.size else None

            if pitch_available and f0_values is not None:
                pitch_slice = _slice_feature_by_time(times, np.asarray(f0_values), start_s, end_s)
                finite_pitch = pitch_slice[np.isfinite(pitch_slice)]
                if finite_pitch.size:
                    row["pitch_median_hz"] = round(float(np.nanmedian(finite_pitch)), 2)
                    row["pitch_std_hz"] = round(float(np.nanstd(finite_pitch)), 2)

        profile["available"] = True
        profile["method"] = "Whisper timestamps + LLDs (ritmo, pausas, RMS, pitch, zcr)"
        profile["summary"].update(
            {
                "sample_rate_hz": sr,
                "voiced_ratio": round(voiced_ratio, 3) if voiced_ratio is not None else None,
                "global_rms_mean": round(float(np.nanmean(rms)), 6) if rms.size else None,
                "global_rms_std": round(float(np.nanstd(rms)), 6) if rms.size else None,
                "global_pitch_median_hz": round(float(np.nanmedian(f0_values[np.isfinite(f0_values)])), 2)
                if pitch_available and f0_values is not None and np.isfinite(f0_values).any()
                else None,
                "global_pitch_std_hz": round(float(np.nanstd(f0_values[np.isfinite(f0_values)])), 2)
                if pitch_available and f0_values is not None and np.isfinite(f0_values).any()
                else None,
            }
        )
    except Exception:
        profile["quality_warnings"].append("No fue posible calcular LLDs del audio con librosa; se mantienen las metricas de Whisper.")


    speed_z = _zscore_list([row["speech_rate_wpm"] for row in segment_rows])
    rms_z = _zscore_list([row["rms_mean"] for row in segment_rows])
    pitch_z = _zscore_list([row["pitch_std_hz"] for row in segment_rows])
    inverse_confidence = [
        (-float(row["avg_logprob"])) if row["avg_logprob"] is not None else None
        for row in segment_rows
    ]
    conf_z = _zscore_list(inverse_confidence)

    raw_intensity: list[float | None] = []
    for idx, row in enumerate(segment_rows):
        score = 0.0
        weight = 0.0
        for item, item_weight in [
            (speed_z[idx], 0.45),
            (rms_z[idx], 0.30),
            (pitch_z[idx], 0.15),
            (conf_z[idx], 0.10),
        ]:
            if item is not None:
                score += float(item) * item_weight
                weight += item_weight
        raw_intensity.append(score / weight if weight else None)

    normalized_intensity = _normalize_to_0_100(raw_intensity)
    for idx, row in enumerate(segment_rows):
        row["intensity_index"] = normalized_intensity[idx]
        if row["intensity_index"] is not None and row["intensity_index"] >= 80:
            if "momento_de_alta_intensidad" not in row["quality_flags"]:
                row["quality_flags"].append("momento_de_alta_intensidad")

    dramatic_pauses = sorted(
        [row for row in segment_rows if row["pause_before_s"] >= PAUSE_DRAMATIC_THRESHOLD_S],
        key=lambda item: (item["pause_before_s"], item["start_s"]),
        reverse=True,
    )

    salient_candidates = sorted(
        segment_rows,
        key=lambda item: (
            item["intensity_index"] if item["intensity_index"] is not None else -1,
            item["pause_before_s"],
        ),
        reverse=True,
    )

    salient_moments = []
    for row in salient_candidates[:5]:
        reasons: list[str] = []
        if row["pause_before_s"] >= PAUSE_DRAMATIC_THRESHOLD_S:
            reasons.append(f"pausa previa {row['pause_before_s']} s")
        if row["speech_rate_wpm"] is not None:
            reasons.append(f"ritmo {row['speech_rate_wpm']:.0f} ppm")
        if row["rms_mean"] is not None:
            reasons.append(f"RMS {row['rms_mean']:.4f}")
        if row["avg_logprob"] is not None and row["avg_logprob"] < LOW_CONFIDENCE_LOGPROB_THRESHOLD:
            reasons.append("confianza tecnica baja")
        if row["elongated_words"]:
            reasons.append("enfasis: " + ", ".join(row["elongated_words"][:3]))
        salient_moments.append(
            {
                "time_label": f"{_seconds_to_label(row['start_s'])}-{_seconds_to_label(row['end_s'])}",
                "text_excerpt": _as_text(row["text"])[:180],
                "intensity_index": row["intensity_index"],
                "reasons": reasons,
            }
        )

    mean_wpm = _safe_mean([row["speech_rate_wpm"] for row in segment_rows])
    mean_logprob = _safe_median([row["avg_logprob"] for row in segment_rows])
    mean_no_speech = _safe_median([row["no_speech_prob"] for row in segment_rows])
    mean_pause = _safe_mean([row["pause_before_s"] for row in segment_rows])
    longest_pause = max([row["pause_before_s"] for row in segment_rows], default=0.0)

    emphasis_words: list[str] = []
    for row in segment_rows:
        for word in row["elongated_words"]:
            if word not in emphasis_words:
                emphasis_words.append(word)

    low_conf_count = sum("logprob_baja" in row["quality_flags"] for row in segment_rows)
    noise_count = sum("ruido_o_silencio_alto" in row["quality_flags"] for row in segment_rows)
    compression_count = sum("compresion_alta" in row["quality_flags"] for row in segment_rows)

    profile["summary"].update(
        {
            "segment_count": len(segment_rows),
            "speech_rate_wpm_mean": round(mean_wpm, 2) if mean_wpm is not None else None,
            "speech_rate_wpm_std": round(_safe_std([row["speech_rate_wpm"] for row in segment_rows]) or 0.0, 2),
            "pause_mean_s": round(mean_pause, 2) if mean_pause is not None else None,
            "pause_longest_s": round(longest_pause, 2),
            "pause_dramatic_count": len(dramatic_pauses),
            "avg_logprob_median": round(mean_logprob, 4) if mean_logprob is not None else None,
            "no_speech_prob_median": round(mean_no_speech, 4) if mean_no_speech is not None else None,
            "low_confidence_segment_count": low_conf_count,
            "noise_alert_segment_count": noise_count,
            "compression_alert_segment_count": compression_count,
            "emphasis_words": emphasis_words[:12],
        }
    )

    profile["quality_warnings"].extend(
        [
            warning
            for warning in [
                f"{low_conf_count} segmentos con avg_logprob < {LOW_CONFIDENCE_LOGPROB_THRESHOLD}" if low_conf_count else "",
                f"{compression_count} segmentos con compression_ratio > {HIGH_COMPRESSION_RATIO_THRESHOLD}" if compression_count else "",
                f"{noise_count} segmentos con no_speech_prob > {HIGH_NO_SPEECH_PROB_THRESHOLD}" if noise_count else "",
            ]
            if warning
        ]
    )
    profile["salient_moments"] = salient_moments
    profile["dramatic_pauses"] = [
        {
            "time_label": f"{_seconds_to_label(row['start_s'])}",
            "pause_before_s": row["pause_before_s"],
            "text_excerpt": _as_text(row["text"])[:180],
        }
        for row in dramatic_pauses[:5]
    ]

    return segment_rows, profile


def build_audio_brief(profile: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    summary = profile.get("summary", {}) if isinstance(profile.get("summary"), dict) else {}
    brief_lines: list[str] = []

    if summary.get("segment_count") is not None:
        brief_lines.append(f"- Segmentos analizados: {summary.get('segment_count')}.")
    if summary.get("speech_rate_wpm_mean") is not None:
        brief_lines.append(
            f"- Ritmo medio estimado: {summary.get('speech_rate_wpm_mean')} ppm "
            f"(desv. {summary.get('speech_rate_wpm_std')})."
        )
    if summary.get("pause_dramatic_count") is not None:
        brief_lines.append(
            f"- Pausas >= {PAUSE_DRAMATIC_THRESHOLD_S} s: {summary.get('pause_dramatic_count')} "
            f"(max. {summary.get('pause_longest_s')} s)."
        )
    if summary.get("global_pitch_median_hz") is not None:
        brief_lines.append(
            f"- Pitch mediano intraaudio: {summary.get('global_pitch_median_hz')} Hz "
            f"(desv. {summary.get('global_pitch_std_hz')} Hz)."
        )
    if summary.get("global_rms_mean") is not None:
        brief_lines.append(
            f"- Energia RMS media: {summary.get('global_rms_mean')} "
            f"(desv. {summary.get('global_rms_std')})."
        )
    if summary.get("low_confidence_segment_count") or summary.get("compression_alert_segment_count") or summary.get("noise_alert_segment_count"):
        brief_lines.append(
            "- Alertas tecnicas: "
            f"logprob baja={summary.get('low_confidence_segment_count', 0)}, "
            f"compresion alta={summary.get('compression_alert_segment_count', 0)}, "
            f"ruido/silencio alto={summary.get('noise_alert_segment_count', 0)}."
        )
    if summary.get("emphasis_words"):
        brief_lines.append("- Palabras enfatizadas por duracion: " + ", ".join(summary.get("emphasis_words", [])[:8]) + ".")

    moments_payload = []
    for item in profile.get("salient_moments", [])[:5]:
        moments_payload.append(
            {
                "momento": item.get("time_label"),
                "intensidad": item.get("intensity_index"),
                "texto": item.get("text_excerpt"),
                "pistas": item.get("reasons", []),
            }
        )

    llm_payload = {
        "nota_metodologica": "Perfil exploratorio de audio. Usar solo como apoyo interpretativo y no como prueba concluyente.",
        "resumen": summary,
        "momentos_destacados": moments_payload,
        "alertas_tecnicas": profile.get("quality_warnings", []),
    }

    brief = "\n".join(brief_lines)
    if not brief:
        brief = "- No se logro construir un perfil de audio suficientemente detallado."
    return brief, llm_payload


def build_segment_export_rows(segment_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    export_rows: list[dict[str, Any]] = []
    for row in segment_rows:
        export_rows.append(
            {
                "segment_id": row.get("segment_id"),
                "inicio_s": row.get("start_s"),
                "fin_s": row.get("end_s"),
                "duracion_s": row.get("duration_s"),
                "texto": row.get("text"),
                "palabras": row.get("word_count"),
                "tokens": row.get("token_count"),
                "pausa_previa_s": row.get("pause_before_s"),
                "velocidad_ppm": row.get("speech_rate_wpm"),
                "avg_logprob": row.get("avg_logprob"),
                "no_speech_prob": row.get("no_speech_prob"),
                "compression_ratio": row.get("compression_ratio"),
                "temperature": row.get("temperature"),
                "rms_mean": row.get("rms_mean"),
                "pitch_median_hz": row.get("pitch_median_hz"),
                "pitch_std_hz": row.get("pitch_std_hz"),
                "zcr_mean": row.get("zcr_mean"),
                "intensity_index": row.get("intensity_index"),
                "elongated_words": ", ".join(row.get("elongated_words", [])),
                "low_prob_words": ", ".join(row.get("low_prob_words", [])),
                "quality_flags": ", ".join(row.get("quality_flags", [])),
            }
        )
    return export_rows


def process_audio_pipeline(
    audio_bytes: bytes,
    engine: str,
    initial_prompt: str,
    file_name: str | None = None,
) -> tuple[
    dict[str, Any],
    str,
    float,
    list[dict[str, Any]],
    dict[str, Any],
    list[dict[str, Any]],
    str,
    dict[str, Any],
    dict[str, Any],
]:
    pipeline_started = time.perf_counter()
    preprocessing_started = time.perf_counter()
    prepared: dict[str, Any] | None = None

    try:
        prepared = prepare_audio_files(audio_bytes, file_name=file_name)
        preprocessing_elapsed_s = round(time.perf_counter() - preprocessing_started, 3)

        api_audio_path = prepared["api_audio_path"]
        analysis_wav_path = prepared["analysis_wav_path"]
        duration_s = float(prepared["duration_s"])
        api_audio_size_bytes = int(prepared["api_audio_size_bytes"])
        analysis_wav_size_bytes = int(prepared["analysis_wav_size_bytes"])

        if engine == "OpenAI Whisper API" and api_audio_size_bytes >= OPENAI_AUDIO_FILE_LIMIT_BYTES:
            raise RuntimeError(
                "El M4A/AAC optimizado excede el limite admitido por la API de OpenAI. "
                f"Tamano del archivo enviado: {_format_bytes(api_audio_size_bytes)}; "
                f"limite configurado: {_format_bytes(OPENAI_AUDIO_FILE_LIMIT_BYTES)}. "
                f"Con {OPENAI_AUDIO_BITRATE_KBPS} kbps, mono y {AUDIO_SAMPLE_RATE_HZ:,} Hz, "
                "el maximo teorico estimado es aproximadamente "
                f"{_seconds_to_label(THEORETICAL_MAX_API_AUDIO_DURATION_S)}."
            )

        if engine != "OpenAI Whisper API":
            raise NotImplementedError(f"El motor '{engine}' no esta implementado.")

        payload, transcription_api_telemetry = transcribe_with_openai_api(
            api_audio_path,
            initial_prompt,
        )
        transcript = _extract_transcript_text(payload)
        if not transcript:
            raise RuntimeError("La transcripcion regreso vacia.")

        normalization_started = time.perf_counter()
        raw_segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
        segment_rows = normalize_segment_rows(raw_segments, transcript)
        normalization_elapsed_s = round(time.perf_counter() - normalization_started, 3)

        features_started = time.perf_counter()
        segment_rows, audio_profile = enrich_segments_with_audio_features(
            analysis_wav_path,
            segment_rows,
        )
        audio_features_elapsed_s = round(time.perf_counter() - features_started, 3)

        segment_export_rows = build_segment_export_rows(segment_rows)
        audio_brief_text, audio_llm_payload = build_audio_brief(audio_profile)

        duration_minutes = duration_s / 60.0
        whisper_cost_usd = duration_minutes * WHISPER_PRICE_PER_MINUTE_USD
        reduction_percent = (
            (1 - api_audio_size_bytes / len(audio_bytes)) * 100
            if audio_bytes
            else None
        )
        telemetry = {
            "file_name": file_name or "audio_sin_nombre",
            "original_size_bytes": len(audio_bytes),
            "api_audio_size_bytes": api_audio_size_bytes,
            "analysis_wav_size_bytes": analysis_wav_size_bytes,
            "api_audio_reduction_percent": round(reduction_percent, 2)
            if reduction_percent is not None
            else None,
            "duration_s": duration_s,
            "duration_minutes": round(duration_minutes, 6),
            "api_audio_format": {
                "container": OPENAI_AUDIO_OUTPUT_FORMAT,
                "codec": OPENAI_AUDIO_CODEC,
                "bitrate_kbps": OPENAI_AUDIO_BITRATE_KBPS,
                "sample_rate_hz": AUDIO_SAMPLE_RATE_HZ,
                "channels": AUDIO_CHANNELS,
            },
            "analysis_wav_format": {
                "codec": "pcm_s16le",
                "sample_rate_hz": WAV_SAMPLE_RATE_HZ,
                "channels": WAV_CHANNELS,
                "sample_width_bits": WAV_SAMPLE_WIDTH_BYTES * 8,
                "purpose": "analisis local con Librosa; no se envia a OpenAI",
            },
            "transcription": {
                **transcription_api_telemetry,
                "billable_minutes": round(duration_minutes, 6),
                "price_per_minute_usd": WHISPER_PRICE_PER_MINUTE_USD,
                "estimated_cost_usd": round(whisper_cost_usd, 8),
                "usage_note": (
                    "whisper-1 se factura por duracion. El archivo enviado es M4A/AAC "
                    f"mono a {AUDIO_SAMPLE_RATE_HZ:,} Hz y {OPENAI_AUDIO_BITRATE_KBPS} kbps."
                ),
            },
            "timings_s": {
                "audio_preprocessing_m4a_and_wav": preprocessing_elapsed_s,
                "transcription_api": transcription_api_telemetry.get("elapsed_s"),
                "segment_normalization": normalization_elapsed_s,
                "audio_features_librosa": audio_features_elapsed_s,
                "audio_pipeline_total": round(time.perf_counter() - pipeline_started, 3),
            },
        }
        return (
            payload,
            transcript,
            duration_s,
            segment_rows,
            audio_profile,
            segment_export_rows,
            audio_brief_text,
            audio_llm_payload,
            telemetry,
        )
    finally:
        if prepared:
            for key in ("input_path", "api_audio_path", "analysis_wav_path"):
                path = prepared.get(key)
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        gc.collect()


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    text = (raw_text or "").strip()
    if not text:
        raise ValueError("La respuesta del modelo vino vacia.")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return json.loads(fenced.group(1))

    braces = re.search(r"\{.*\}", text, re.DOTALL)
    if braces:
        return json.loads(braces.group(0))

    raise ValueError("No se pudo extraer un objeto JSON valido de la respuesta del modelo.")


def _extract_usage_telemetry(usage: Any) -> dict[str, int]:
    usage_dict = _coerce_to_dict(usage) if usage is not None else {}
    prompt_details = usage_dict.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    completion_details = usage_dict.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}

    prompt_tokens = _as_int(usage_dict.get("prompt_tokens"), 0)
    completion_tokens = _as_int(usage_dict.get("completion_tokens"), 0)
    total_tokens = _as_int(usage_dict.get("total_tokens"), prompt_tokens + completion_tokens)
    cached_tokens = _as_int(
        prompt_details.get("cached_tokens", usage_dict.get("cached_tokens")),
        0,
    )
    cache_write_tokens = _as_int(
        prompt_details.get("cache_write_tokens", usage_dict.get("cache_write_tokens")),
        0,
    )
    reasoning_tokens = _as_int(
        completion_details.get("reasoning_tokens", usage_dict.get("reasoning_tokens")),
        0,
    )
    return {
        "input_tokens": prompt_tokens,
        "cached_input_tokens": cached_tokens,
        "cache_write_tokens": cache_write_tokens,
        "output_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "total_tokens": total_tokens,
    }


def estimate_gpt56_luna_cost(usage: dict[str, int]) -> dict[str, Any]:
    input_tokens = max(0, _as_int(usage.get("input_tokens"), 0))
    cached_tokens = max(0, _as_int(usage.get("cached_input_tokens"), 0))
    cache_write_tokens = max(0, _as_int(usage.get("cache_write_tokens"), 0))
    output_tokens = max(0, _as_int(usage.get("output_tokens"), 0))

    is_long_context = input_tokens > GPT_LONG_CONTEXT_THRESHOLD_TOKENS
    if is_long_context:
        input_rate = GPT_LONG_INPUT_PRICE_PER_1M
        cached_rate = GPT_LONG_CACHED_INPUT_PRICE_PER_1M
        cache_write_rate = GPT_LONG_CACHE_WRITE_PRICE_PER_1M
        output_rate = GPT_LONG_OUTPUT_PRICE_PER_1M
        context_tier = "long_context"
    else:
        input_rate = GPT_SHORT_INPUT_PRICE_PER_1M
        cached_rate = GPT_SHORT_CACHED_INPUT_PRICE_PER_1M
        cache_write_rate = GPT_SHORT_CACHE_WRITE_PRICE_PER_1M
        output_rate = GPT_SHORT_OUTPUT_PRICE_PER_1M
        context_tier = "short_context"

    regular_input_tokens = max(0, input_tokens - cached_tokens - cache_write_tokens)
    input_cost = regular_input_tokens / 1_000_000 * input_rate
    cached_cost = cached_tokens / 1_000_000 * cached_rate
    cache_write_cost = cache_write_tokens / 1_000_000 * cache_write_rate
    output_cost = output_tokens / 1_000_000 * output_rate
    total_cost = input_cost + cached_cost + cache_write_cost + output_cost

    return {
        "context_tier": context_tier,
        "long_context_threshold_tokens": GPT_LONG_CONTEXT_THRESHOLD_TOKENS,
        "regular_input_tokens": regular_input_tokens,
        "rates_usd_per_1m": {
            "input": input_rate,
            "cached_input": cached_rate,
            "cache_write": cache_write_rate,
            "output": output_rate,
        },
        "cost_breakdown_usd": {
            "input": round(input_cost, 8),
            "cached_input": round(cached_cost, 8),
            "cache_write": round(cache_write_cost, 8),
            "output": round(output_cost, 8),
        },
        "estimated_cost_usd": round(total_cost, 8),
    }


def call_openai_json(
    system_prompt: str,
    user_prompt: str,
    model_name: str,
) -> tuple[str, dict[str, Any]]:
    client = get_openai_client()
    requested_model = OPENAI_ANALYSIS_MODEL if model_name == "OpenAI GPT-5.6-luna" else model_name
    started = time.perf_counter()

    response = client.chat.completions.create(
        model=requested_model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    elapsed_s = round(time.perf_counter() - started, 3)
    content = response.choices[0].message.content
    if not content:
        raise RuntimeError("OpenAI no devolvio contenido en la respuesta de analisis.")

    raw_usage = (
        _coerce_to_dict(getattr(response, "usage", None))
        if getattr(response, "usage", None) is not None
        else {}
    )
    usage = _extract_usage_telemetry(raw_usage)
    if usage["input_tokens"] <= 0:
        usage["input_tokens"] = math.ceil((len(system_prompt) + len(user_prompt)) / 4)
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        token_source = "estimacion_por_caracteres"
    else:
        token_source = "usage_devuelto_por_openai"

    cost = estimate_gpt56_luna_cost(usage)
    telemetry = {
        "model_requested": requested_model,
        "model_returned": _as_text(getattr(response, "model", None), requested_model),
        "request_id": getattr(response, "_request_id", None),
        "elapsed_s": elapsed_s,
        "system_prompt_characters": len(system_prompt),
        "user_prompt_characters": len(user_prompt),
        "input_characters_total": len(system_prompt) + len(user_prompt),
        "output_visible_characters": len(content),
        "usage": usage,
        "usage_raw_returned_by_openai": raw_usage,
        "token_source": token_source,
        "pricing": cost,
    }
    return content, telemetry


def execute_llm_call(
    llm_engine: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    raw_text, telemetry = call_openai_json(
        system_prompt,
        user_prompt,
        model_name=llm_engine,
    )
    return _extract_json_object(raw_text), raw_text, telemetry


def normalize_relevance_payload(payload: dict[str, Any]) -> dict[str, Any]:
    density_score = _as_int(payload.get("density_score"), fallback=0)
    density_score = max(0, min(100, density_score))
    relevant = _as_bool(payload.get("relevant"), fallback=density_score >= 51)
    return {
        "relevant": relevant,
        "density_score": density_score,
        "reason": _as_text(payload.get("reason"), "No se entrego razon."),
        "criteria_met": _as_list(payload.get("criteria_met")),
        "criteria_missing": _as_list(payload.get("criteria_missing")),
        "key_actors_detected": _as_list(payload.get("key_actors_detected")),
        "key_topics_detected": _as_list(payload.get("key_topics_detected")),
        "missing_context_for_analysis": _as_list(payload.get("missing_context_for_analysis")),
    }


def _normalize_evidence_block(item: Any, description_fallback: str = "no identificable") -> dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    return {
        "description": _as_text(item.get("description"), description_fallback),
        "evidence": _as_text(item.get("evidence")),
    }


def _normalize_relation_of_force(item: Any) -> dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    return {
        "with_actor": _as_text(item.get("with_actor"), "no identificable"),
        "type": _as_text(item.get("type"), "no identificable"),
        "evidence": _as_text(item.get("evidence")),
    }


def _normalize_actor(item: Any) -> dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    repertoire = item.get("repertoire") if isinstance(item.get("repertoire"), dict) else {}
    relations = item.get("relations_of_force") if isinstance(item.get("relations_of_force"), list) else []
    recommendation = item.get("strategic_recommendation") if isinstance(item.get("strategic_recommendation"), dict) else {}
    return {
        "name": _as_text(item.get("name"), "no identificable"),
        "type": _as_text(item.get("type"), "no identificable"),
        "stance_on_change": _as_text(item.get("stance_on_change"), "no_determinada"),
        "repertoire": {
            "interests": _as_list(repertoire.get("interests")),
            "principles": _as_list(repertoire.get("principles")),
            "resources": _as_list(repertoire.get("resources")),
        },
        "relations_of_force": [_normalize_relation_of_force(rel) for rel in relations],
        "key_quote": _as_text(item.get("key_quote")),
        "strategic_recommendation": {
            "suggestion": _as_text(recommendation.get("suggestion")),
            "based_on": _as_text(recommendation.get("based_on")),
        },
    }


def _normalize_shared_recommendation(item: Any) -> dict[str, Any]:
    item = item if isinstance(item, dict) else {}
    return {
        "for_actors": _as_list(item.get("for_actors")),
        "suggestion": _as_text(item.get("suggestion")),
        "based_on": _as_text(item.get("based_on")),
    }


def normalize_analysis_payload(payload: dict[str, Any], prompt_profile_name: str) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    time_block = payload.get("time") if isinstance(payload.get("time"), dict) else {}
    structure = payload.get("structure_coyuntura") if isinstance(payload.get("structure_coyuntura"), dict) else {}
    events = payload.get("events") if isinstance(payload.get("events"), dict) else {}
    scenarios = payload.get("scenarios") if isinstance(payload.get("scenarios"), dict) else {}
    dispute = payload.get("dispute_of_meaning") if isinstance(payload.get("dispute_of_meaning"), dict) else {}
    frontier = dispute.get("friend_enemy_frontier") if isinstance(dispute.get("friend_enemy_frontier"), dict) else {}

    resignifications = []
    for item in (
        dispute.get("resignification_of_concepts")
        if isinstance(dispute.get("resignification_of_concepts"), list)
        else []
    ):
        item = item if isinstance(item, dict) else {}
        readings = []
        for reading in (item.get("readings") if isinstance(item.get("readings"), list) else []):
            reading = reading if isinstance(reading, dict) else {}
            readings.append(
                {
                    "actor": _as_text(reading.get("actor"), "no identificable"),
                    "meaning": _as_text(reading.get("meaning"), "no identificable"),
                }
            )
        resignifications.append(
            {
                "concept": _as_text(item.get("concept"), "no identificable"),
                "readings": readings,
                "evidence": _as_text(item.get("evidence")),
            }
        )

    return {
        "meta": {
            "date_analyzed": _as_text(meta.get("date_analyzed"), datetime.now().strftime("%Y-%m-%d")),
            "methodology": _as_text(
                meta.get("methodology"),
                "DSRM + De Souza + Nieto + Zamitiz + Errejon + Fazio + Licha + Garcia + Schmitt",
            ),
            "confidence_level": _as_text(meta.get("confidence_level"), "medio"),
            "prompt_profile": _as_text(meta.get("prompt_profile"), prompt_profile_name),
        },
        "summary": _as_text(payload.get("summary"), "No se genero resumen."),
        "time": {
            "long_duration": _normalize_evidence_block(time_block.get("long_duration")),
            "medium_duration": _normalize_evidence_block(time_block.get("medium_duration")),
            "short_duration": _normalize_evidence_block(time_block.get("short_duration")),
        },
        "structure_coyuntura": {
            "economic": _normalize_evidence_block(structure.get("economic")),
            "political": _normalize_evidence_block(structure.get("political")),
            "social": _normalize_evidence_block(structure.get("social")),
        },
        "events": {
            "trigger_event": _normalize_evidence_block(events.get("trigger_event")),
            "derived_events": [
                _normalize_evidence_block(item)
                for item in (events.get("derived_events") if isinstance(events.get("derived_events"), list) else [])
            ],
            "background_trends": [
                _normalize_evidence_block(item)
                for item in (events.get("background_trends") if isinstance(events.get("background_trends"), list) else [])
            ],
        },
        "scenarios": {
            "public_institutional": _normalize_evidence_block(scenarios.get("public_institutional")),
            "public_social": _normalize_evidence_block(scenarios.get("public_social")),
            "private": _normalize_evidence_block(scenarios.get("private")),
            "international": _normalize_evidence_block(scenarios.get("international")),
        },
        "actors": [
            _normalize_actor(actor)
            for actor in (payload.get("actors") if isinstance(payload.get("actors"), list) else [])
        ],
        "dispute_of_meaning": {
            "resignification_of_concepts": resignifications,
            "friend_enemy_frontier": {
                "us_construction": _as_text(frontier.get("us_construction"), "no identificable"),
                "them_construction": _as_text(frontier.get("them_construction"), "no identificable"),
                "evidence": _as_text(frontier.get("evidence")),
            },
        },
        "shared_recommendations": [
            _normalize_shared_recommendation(item)
            for item in (
                payload.get("shared_recommendations")
                if isinstance(payload.get("shared_recommendations"), list)
                else []
            )
        ],
        "limitations": _as_text(payload.get("limitations"), "No se especificaron limitaciones."),
    }


def build_llm_user_payload(transcript: str, initial_context: str, audio_llm_payload: dict[str, Any], task_label: str) -> str:
    audio_json = json.dumps(audio_llm_payload, ensure_ascii=False, indent=2)
    return (
        f"{task_label}\n\n"
        f"CONTEXTO INICIAL DEL USUARIO:\n{initial_context}\n\n"
        "PERFIL EXPLORATORIO DE AUDIO (usar con prudencia metodologica):\n"
        f"{audio_json}\n\n"
        f"TRANSCRIPCION:\n{transcript}"
    )


def run_relevance_filter(
    transcript: str,
    initial_context: str,
    audio_llm_payload: dict[str, Any],
    llm_engine: str,
    relevance_prompt: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    user_prompt = build_llm_user_payload(
        transcript=transcript,
        initial_context=initial_context,
        audio_llm_payload=audio_llm_payload,
        task_label="Evalua la densidad politica de la transcripcion.",
    )
    payload, raw, telemetry = execute_llm_call(llm_engine, relevance_prompt, user_prompt)
    telemetry["stage"] = "relevance_filter"
    return normalize_relevance_payload(payload), raw, telemetry


def run_strategic_analysis(
    transcript: str,
    initial_context: str,
    audio_llm_payload: dict[str, Any],
    llm_engine: str,
    analysis_prompt: str,
    prompt_profile_name: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    user_prompt = build_llm_user_payload(
        transcript=transcript,
        initial_context=initial_context,
        audio_llm_payload=audio_llm_payload,
        task_label="Realiza el analisis estrategico de coyuntura.",
    )
    payload, raw, telemetry = execute_llm_call(llm_engine, analysis_prompt, user_prompt)
    telemetry["stage"] = "strategic_analysis"
    return normalize_analysis_payload(payload, prompt_profile_name), raw, telemetry


class PoliticalReportPDF(FPDF):
    PRIMARY = (15, 23, 42)
    SECONDARY = (71, 85, 105)
    MUTED = (100, 116, 139)
    BORDER = (203, 213, 225)
    LIGHT = (248, 250, 252)
    SOFT = (241, 245, 249)
    ACCENT = (239, 68, 68)
    GOOD = (22, 101, 52)
    BAD = (153, 27, 27)

    def _text(self, rgb: tuple[int, int, int]) -> None:
        self.set_text_color(*rgb)

    def _fill(self, rgb: tuple[int, int, int]) -> None:
        self.set_fill_color(*rgb)

    def _draw(self, rgb: tuple[int, int, int]) -> None:
        self.set_draw_color(*rgb)

    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self._text(self.MUTED)
        self.cell(0, 7, self.safe("Informe de analisis politico de coyuntura"), ln=1, align="R")
        self._draw(self.BORDER)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def footer(self) -> None:
        self.set_y(-13)
        self.set_font("Helvetica", "", 8)
        self._text(self.MUTED)
        self.cell(0, 8, self.safe(f"Pagina {self.page_no()}"), align="C")

    @staticmethod
    def safe(text: Any) -> str:
        # Se remueven caracteres tipograficos que corrompen la codificacion en latin-1
        replacements = {
            "\u2018": "'",
            "\u2019": "'",
            "\u201c": '"',
            "\u201d": '"',
            "\u2013": "-",
            "\u2014": "--",
            "\u2026": "...",
            "\xa0": " ",
            "→": "->",
            "≥": ">=",
            "≤": "<=",
        }
        clean = str(text if text is not None else "")
        for old, new in replacements.items():
            clean = clean.replace(old, new)
        return clean.encode("latin-1", errors="replace").decode("latin-1")

    def ensure_space(self, height: float) -> None:
        if self.get_y() + height > self.h - self.b_margin:
            self.add_page()

    def cover_page(
        self,
        prompt_profile_name: str,
        confidence_level: str,
        density_score: int,
        initial_context: str,
    ) -> None:
        self._fill(self.PRIMARY)
        self.rect(0, 0, self.w, 62, "F")
        self._fill(self.ACCENT)
        self.rect(0, 0, self.w, 4, "F")

        self.set_xy(14, 16)
        self.set_font("Helvetica", "B", 19)
        self._text((255, 255, 255))
        self.multi_cell(0, 8.5, self.safe(APP_TITLE))
        self.set_x(14)
        self.set_font("Helvetica", "", 10.5)
        self._text((226, 232, 240))
        self.multi_cell(0, 5.5, self.safe(APP_DESCRIPTION))
        self.set_x(14)
        self.multi_cell(0, 5.5, self.safe(APP_SUBTITLE))

        self.set_y(74)
        self.kpi_row(
            [
                ("Densidad politica", f"{density_score}/100"),
                ("Confianza", confidence_level or "medio"),
                ("Perfil", prompt_profile_name),
                ("Fecha", datetime.now().strftime("%Y-%m-%d")),
            ]
        )
        self.ln(5)
        self.note_box(
            "Contexto inicial declarado",
            initial_context or "No proporcionado.",
            fill=self.LIGHT,
        )
        self.note_box(
            "Criterio de lectura",
            "Este informe es una salida asistida por IA para ordenar acontecimientos, escenarios, actores, relacion de fuerzas y disputa de sentidos. Debe triangularse con la transcripcion y la revision humana.",
            fill=(255, 247, 237),
        )

    def kpi_row(self, items: list[tuple[str, Any]]) -> None:
        self.ensure_space(25)
        gap = 3
        width = (self.w - self.l_margin - self.r_margin - gap * (len(items) - 1)) / len(items)
        y = self.get_y()
        x = self.l_margin
        for label, value in items:
            self._draw(self.BORDER)
            self._fill(self.LIGHT)
            self.rect(x, y, width, 22, "DF")
            self.set_xy(x + 3, y + 3)
            self.set_font("Helvetica", "B", 7.5)
            self._text(self.MUTED)
            self.cell(width - 6, 4, self.safe(label), ln=1)
            self.set_x(x + 3)
            self.set_font("Helvetica", "B", 11)
            self._text(self.PRIMARY)
            self.multi_cell(width - 6, 5, self.safe(value))
            x += width + gap
        self.set_y(y + 25)

    def section_title(self, title: str) -> None:
        self.ensure_space(18)
        self.ln(2)
        y = self.get_y()
        self._fill(self.ACCENT)
        self.rect(self.l_margin, y + 1.5, 2.2, 8, "F")
        self.set_x(self.l_margin + 5)
        self.set_font("Helvetica", "B", 13)
        self._text(self.PRIMARY)
        self.cell(0, 8, self.safe(title), ln=1)
        self._draw(self.BORDER)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(3)

    def subsection_title(self, title: str) -> None:
        self.ensure_space(12)
        self.set_font("Helvetica", "B", 10.5)
        self._text(self.PRIMARY)
        self.cell(0, 6, self.safe(title), ln=1)

    def body_text(self, text: Any, line_height: float = 5.4) -> None:
        text = self.safe(text).strip()
        if not text:
            return
        self.set_font("Helvetica", "", 9.6)
        self._text(self.PRIMARY)
        for paragraph in text.split("\n"):
            paragraph = paragraph.strip()
            if not paragraph:
                self.ln(1.5)
                continue
            self.multi_cell(0, line_height, paragraph)
        self.ln(1)

    def muted_text(self, text: Any) -> None:
        self.set_font("Helvetica", "", 8.8)
        self._text(self.MUTED)
        self.multi_cell(0, 4.8, self.safe(text))
        self.ln(1)

    def note_box(
        self,
        title: str,
        text: Any,
        fill: tuple[int, int, int] | None = None,
        border: tuple[int, int, int] | None = None,
    ) -> None:
        fill = fill or self.LIGHT
        border = border or self.BORDER
        text = self.safe(text)
        available_w = self.w - self.l_margin - self.r_margin
        line_count = max(2, int(len(text) / 95) + text.count("\n") + 1)
        height = 10 + line_count * 4.8
        self.ensure_space(height + 4)
        x = self.l_margin
        y = self.get_y()
        self._draw(border)
        self._fill(fill)
        self.rect(x, y, available_w, height, "DF")
        self.set_xy(x + 4, y + 3)
        self.set_font("Helvetica", "B", 9.5)
        self._text(self.PRIMARY)
        self.cell(available_w - 8, 5, self.safe(title), ln=1)
        self.set_x(x + 4)
        self.set_font("Helvetica", "", 8.8)
        self._text(self.SECONDARY)
        self.multi_cell(available_w - 8, 4.7, text)
        self.set_y(y + height + 4)

    def bullets(self, items: list[str]) -> None:
        if not items:
            return
        self.set_font("Helvetica", "", 9.4)
        self._text(self.PRIMARY)
        for item in items:
            self.ensure_space(8)
            self.set_x(self.l_margin + 3)
            self.multi_cell(0, 5.2, self.safe(f"- {item}"))
        self.ln(1)

    def actor_card(self, actor: dict[str, Any]) -> None:
        self.ensure_space(36)
        x = self.l_margin
        y = self.get_y()
        w = self.w - self.l_margin - self.r_margin
        self._draw(self.BORDER)
        self._fill(self.LIGHT)
        self.rect(x, y, w, 12, "DF")
        self.set_xy(x + 4, y + 3)
        self.set_font("Helvetica", "B", 10.5)
        self._text(self.PRIMARY)
        self.cell(w - 8, 5, self.safe(actor.get("name", "no identificable")), ln=1)
        self.set_y(y + 14)
        self.body_text(
            f"Tipo: {actor.get('type', 'no identificable')} | Postura frente al cambio: {actor.get('stance_on_change', 'no identificable')}"
        )
        repertoire = actor.get("repertoire", {}) if isinstance(actor.get("repertoire"), dict) else {}
        details = [
            ("Intereses", ", ".join(repertoire.get("interests", [])) or "no identificable"),
            ("Principios", ", ".join(repertoire.get("principles", [])) or "no identificable"),
            ("Recursos", ", ".join(repertoire.get("resources", [])) or "no identificable"),
        ]
        for label, value in details:
            self.body_text(f"{label}: {value}", line_height=5.0)

        relations = actor.get("relations_of_force", []) if isinstance(actor.get("relations_of_force"), list) else []
        if relations:
            self.subsection_title("Relaciones de fuerza")
            for rel in relations:
                self.body_text(
                    f"- {rel.get('type', 'no identificable')} con {rel.get('with_actor', 'no identificable')}",
                    line_height=5.0,
                )
                if rel.get("evidence"):
                    self.muted_text(f'"{rel.get("evidence")}"')

        if actor.get("key_quote"):
            self.note_box("Cita clave detectada", actor.get("key_quote"), fill=self.SOFT)

        recommendation = actor.get("strategic_recommendation", {}) if isinstance(actor.get("strategic_recommendation"), dict) else {}
        if recommendation.get("suggestion"):
            self.note_box("Recomendacion estrategica (borrador)", recommendation.get("suggestion"), fill=(240, 253, 244), border=(187, 247, 208))
        self.ln(2)


def build_pdf(
    analysis: dict[str, Any],
    relevance: dict[str, Any],
    transcript: str,
    prompt_profile_name: str,
    initial_context: str,
    audio_profile: dict[str, Any],
) -> bytes:
    pdf = PoliticalReportPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(13, 14, 13)
    pdf.add_page()

    meta = analysis.get("meta", {}) if isinstance(analysis.get("meta"), dict) else {}
    pdf.cover_page(
        prompt_profile_name=prompt_profile_name,
        confidence_level=meta.get("confidence_level", "medio"),
        density_score=int(relevance.get("density_score", 0) or 0),
        initial_context=initial_context,
    )

    pdf.add_page()
    pdf.section_title("1. Resumen ejecutivo")
    pdf.body_text(analysis.get("summary", ""))
    pdf.note_box(
        "Decision del filtro de relevancia",
        f"Relevante: {relevance.get('relevant', False)} | Densidad politica: {relevance.get('density_score', 0)}/100 | Razon: {relevance.get('reason', '')}",
        fill=(240, 253, 244) if relevance.get("relevant") else (254, 242, 242),
        border=(187, 247, 208) if relevance.get("relevant") else (254, 202, 202),
    )

    pdf.section_title("2. Perfil de audio y confiabilidad")
    pdf.body_text(
        "El modulo de audio agrega una capa metodologica previa al LLM: tiempos, pausas, velocidad, logprob, compresion y, cuando es posible, descriptores de bajo nivel como energia RMS y pitch. La lectura es exploratoria y debe triangularse con el texto."
    )
    audio_summary = audio_profile.get("summary", {}) if isinstance(audio_profile.get("summary"), dict) else {}
    pdf.kpi_row(
        [
            ("Segmentos", audio_summary.get("segment_count", "N/A")),
            ("Ritmo medio", f"{audio_summary.get('speech_rate_wpm_mean')} ppm" if audio_summary.get("speech_rate_wpm_mean") is not None else "N/A"),
            ("Pausas >= 1.5s", audio_summary.get("pause_dramatic_count", "N/A")),
            ("Alertas", int(audio_summary.get("low_confidence_segment_count", 0)) + int(audio_summary.get("compression_alert_segment_count", 0)) + int(audio_summary.get("noise_alert_segment_count", 0))),
        ]
    )
    audio_lines: list[str] = []
    if audio_summary.get("pause_longest_s") is not None:
        audio_lines.append(f"Pausa mas larga: {audio_summary.get('pause_longest_s')} s")
    if audio_summary.get("global_pitch_median_hz") is not None:
        audio_lines.append(
            f"Pitch mediano intraaudio: {audio_summary.get('global_pitch_median_hz')} Hz (desv. {audio_summary.get('global_pitch_std_hz')} Hz)"
        )
    if audio_summary.get("global_rms_mean") is not None:
        audio_lines.append(f"RMS medio: {audio_summary.get('global_rms_mean')} (desv. {audio_summary.get('global_rms_std')})")
    if audio_summary.get("emphasis_words"):
        audio_lines.append("Palabras enfatizadas por duracion: " + ", ".join(audio_summary.get("emphasis_words", [])[:10]))
    if audio_lines:
        pdf.note_box("Sintesis tecnica del audio", "\n".join(audio_lines), fill=pdf.LIGHT)

    if audio_profile.get("salient_moments"):
        pdf.subsection_title("Momentos destacados para lectura reforzada")
        pdf.bullets(
            [
                f"{item.get('time_label')}: {item.get('text_excerpt')} | pistas: {', '.join(item.get('reasons', []))}"
                for item in audio_profile.get("salient_moments", [])[:5]
            ]
        )
    if audio_profile.get("quality_warnings"):
        pdf.note_box("Alertas metodologicas", "\n".join(audio_profile.get("quality_warnings", [])), fill=(255, 247, 237), border=(253, 186, 116))

    pdf.section_title("3. Filtro de relevancia politica")
    pdf.subsection_title("Criterios cumplidos")
    pdf.bullets(relevance.get("criteria_met", []))
    pdf.subsection_title("Criterios faltantes o contexto por robustecer")
    missing_items = relevance.get("criteria_missing", []) + relevance.get("missing_context_for_analysis", [])
    pdf.bullets(missing_items or ["No se reportaron vacios criticos de contexto."])

    pdf.section_title("4. Tiempo y estructura/coyuntura")
    time_block = analysis.get("time", {}) if isinstance(analysis.get("time"), dict) else {}
    structure = analysis.get("structure_coyuntura", {}) if isinstance(analysis.get("structure_coyuntura"), dict) else {}
    pdf.subsection_title("Tiempo")
    pdf.body_text(f"Larga duracion: {time_block.get('long_duration', {}).get('description', 'no identificable')}")
    pdf.body_text(f"Mediana duracion: {time_block.get('medium_duration', {}).get('description', 'no identificable')}")
    pdf.body_text(f"Corta duracion: {time_block.get('short_duration', {}).get('description', 'no identificable')}")
    pdf.subsection_title("Estructura/coyuntura")
    pdf.body_text(f"Economica: {structure.get('economic', {}).get('description', 'no identificable')}")
    pdf.body_text(f"Politica: {structure.get('political', {}).get('description', 'no identificable')}")
    pdf.body_text(f"Social: {structure.get('social', {}).get('description', 'no identificable')}")

    pdf.section_title("5. Acontecimientos y escenarios")
    events = analysis.get("events", {}) if isinstance(analysis.get("events"), dict) else {}
    scenarios = analysis.get("scenarios", {}) if isinstance(analysis.get("scenarios"), dict) else {}
    pdf.note_box("Acontecimiento desencadenante", events.get("trigger_event", {}).get("description", "no identificable"), fill=pdf.SOFT)
    pdf.subsection_title("Eventos derivados")
    pdf.bullets([item.get("description", "no identificable") for item in events.get("derived_events", [])])
    pdf.subsection_title("Tendencias de fondo")
    pdf.bullets([item.get("description", "no identificable") for item in events.get("background_trends", [])])
    pdf.subsection_title("Escenarios")
    pdf.body_text(f"Publico institucional: {scenarios.get('public_institutional', {}).get('description', 'no identificable')}")
    pdf.body_text(f"Publico social: {scenarios.get('public_social', {}).get('description', 'no identificable')}")
    pdf.body_text(f"Privado: {scenarios.get('private', {}).get('description', 'no identificable')}")
    pdf.body_text(f"Internacional: {scenarios.get('international', {}).get('description', 'no identificable')}")

    pdf.section_title("6. Mapa de actores")
    actors = analysis.get("actors", []) if isinstance(analysis.get("actors"), list) else []
    if not actors:
        pdf.body_text("No se identificaron actores con suficiente claridad.")
    else:
        for actor in actors:
            pdf.actor_card(actor)

    pdf.section_title("7. Disputa de sentidos")
    dispute = analysis.get("dispute_of_meaning", {}) if isinstance(analysis.get("dispute_of_meaning"), dict) else {}
    resignifications = dispute.get("resignification_of_concepts", []) if isinstance(dispute.get("resignification_of_concepts"), list) else []
    if resignifications:
        pdf.subsection_title("Resignificacion de conceptos")
        for item in resignifications:
            readings = "; ".join(
                f"{r.get('actor', 'no identificable')} = {r.get('meaning', 'no identificable')}"
                for r in item.get("readings", [])
            )
            pdf.body_text(f"- {item.get('concept', 'no identificable')}: {readings or 'no identificable'}")
    else:
        pdf.body_text("No se identifico resignificacion de conceptos.")
    frontier = dispute.get("friend_enemy_frontier", {}) if isinstance(dispute.get("friend_enemy_frontier"), dict) else {}
    pdf.subsection_title("Frontera amigo/enemigo")
    pdf.body_text(f"Construccion del nosotros: {frontier.get('us_construction', 'no identificable')}")
    pdf.body_text(f"Construccion del ellos: {frontier.get('them_construction', 'no identificable')}")

    pdf.section_title("8. Recomendaciones compartidas, limites y triangulacion")
    shared = analysis.get("shared_recommendations", []) if isinstance(analysis.get("shared_recommendations"), list) else []
    if shared:
        for item in shared:
            actors_label = ", ".join(item.get("for_actors", [])) or "no identificable"
            pdf.subsection_title(f"Para: {actors_label}")
            pdf.body_text(item.get("suggestion", "no identificable"))
            if item.get("based_on"):
                pdf.muted_text(f"Basado en: {item.get('based_on')}")
    else:
        pdf.body_text("No se identificaron relaciones de cooperacion que ameriten una recomendacion compartida. Las recomendaciones individuales estan en la ficha de cada actor (seccion 6).")
    pdf.note_box("Limitaciones declaradas", analysis.get("limitations", "No se especificaron limitaciones."), fill=(248, 250, 252))

    pdf.add_page()
    pdf.section_title("Anexo. Transcripcion completa")
    pdf.body_text("Se incluye la transcripcion original para triangulacion humana y control de rigor disciplinar.")
    pdf.body_text(transcript)

    rendered = pdf.output(dest="S")
    return rendered.encode("latin-1") if isinstance(rendered, str) else bytes(rendered)


def build_intensity_timeline(segment_rows: list[dict[str, Any]]) -> go.Figure | None:
    if not segment_rows:
        return None
    df = pd.DataFrame(segment_rows)
    df["start_label"] = df["start_s"].apply(_seconds_to_label)
    hover_text = []
    for _, row in df.iterrows():
        hover_text.append(
            "<br>".join(
                [
                    f"<b>{row['start_label']}</b>",
                    f"Texto: {str(row.get('text', ''))[:120]}",
                    f"Intensidad: {row.get('intensity_index')}",
                    f"Pausa previa: {row.get('pause_before_s')} s",
                    f"Ritmo: {row.get('speech_rate_wpm')} ppm",
                    f"avg_logprob: {row.get('avg_logprob')}",
                ]
            )
        )

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=df["start_s"],
            y=df["intensity_index"],
            mode="lines+markers",
            name="Intensidad",
            text=hover_text,
            hoverinfo="text",
            line=dict(width=3),
        )
    )
    dramatic_df = df[df["pause_before_s"] >= PAUSE_DRAMATIC_THRESHOLD_S]
    if not dramatic_df.empty:
        fig.add_trace(
            go.Scatter(
                x=dramatic_df["start_s"],
                y=dramatic_df["intensity_index"],
                mode="markers",
                name="Pausa dramatica",
                marker=dict(size=11, symbol="diamond"),
                text=[
                    f"{_seconds_to_label(item['start_s'])} | pausa {item['pause_before_s']} s<br>{str(item['text'])[:120]}"
                    for _, item in dramatic_df.iterrows()
                ],
                hoverinfo="text",
            )
        )

    fig.update_layout(
        height=320,
        margin=dict(l=20, r=20, t=10, b=10),
        xaxis_title="Tiempo (s)",
        yaxis_title="Indice exploratorio de intensidad",
        legend=dict(orientation="h", x=0, y=1.12),
        paper_bgcolor="white",
        plot_bgcolor="white",
    )
    return fig


def html_safe(value: Any) -> str:
    text = str(value)
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def render_kpi_card(label: str, value: Any, note: str = "") -> None:
    note_html = f"<div class='note'>{html_safe(note)}</div>" if note else ""
    st.markdown(
        f"<div class='result-kpi'><div class='label'>{html_safe(label)}</div>"
        f"<div class='value'>{html_safe(value)}</div>{note_html}</div>",
        unsafe_allow_html=True,
    )


def render_list(title: str, items: list[str], empty_text: str = "No identificable.") -> None:
    st.markdown(f"**{title}**")
    if not items:
        st.write(empty_text)
        return
    for item in items:
        st.markdown(f"- {item}")


def render_actor_table(actors: list[dict[str, Any]]) -> None:
    if not actors:
        st.info("No se identificaron actores con suficiente claridad.")
        return

    rows = []
    for actor in actors:
        repertoire = actor.get("repertoire", {}) if isinstance(actor.get("repertoire"), dict) else {}
        relations = actor.get("relations_of_force", []) if isinstance(actor.get("relations_of_force"), list) else []
        relations_label = "; ".join(f"{r.get('type', '')} con {r.get('with_actor', '')}" for r in relations)
        rows.append(
            {
                "Actor": actor.get("name", "no identificable"),
                "Tipo": actor.get("type", "no identificable"),
                "Postura frente al cambio": actor.get("stance_on_change", "no identificable"),
                "Intereses": ", ".join(repertoire.get("interests", [])),
                "Principios": ", ".join(repertoire.get("principles", [])),
                "Recursos": ", ".join(repertoire.get("resources", [])),
                "Relaciones de fuerza": relations_label or "no identificable",
            }
        )
    st.dataframe(rows, width="stretch", hide_index=True)

    with st.expander("Citas clave detectadas"):
        for actor in actors:
            if actor.get("key_quote"):
                st.markdown(f"**{actor['name']}**")
                st.write(actor["key_quote"])

    with st.expander("Recomendaciones estrategicas individuales (borrador)"):
        any_recommendation = False
        for actor in actors:
            suggestion = actor.get("strategic_recommendation", {}).get("suggestion")
            if suggestion:
                any_recommendation = True
                st.markdown(f"**{actor['name']}**")
                st.write(suggestion)
                based_on = actor.get("strategic_recommendation", {}).get("based_on")
                if based_on:
                    st.caption(f"Basado en: {based_on}")
        if not any_recommendation:
            st.write("No se generaron recomendaciones individuales.")


def render_audio_profile(audio_profile: dict[str, Any], segment_rows: list[dict[str, Any]], segment_export_rows: list[dict[str, Any]]) -> None:
    if not audio_profile:
        st.info("Todavia no hay evidencia de audio calculada.")
        return

    summary = audio_profile.get("summary", {}) if isinstance(audio_profile.get("summary"), dict) else {}
    st.markdown(
        "<div class='app-note'><strong>Nota metodologica:</strong> este modulo no intenta etiquetar emociones "
        "discretas. Su apuesta es mas sobria: aprovechar Whisper y el audio para extraer pausas, ritmo, confianza "
        "tecnica y LLDs interpretables (pitch/energia) que luego se triangulan con el texto.</div>",
        unsafe_allow_html=True,
    )

    kpi_cols = st.columns(5)
    metrics = [
        ("Segmentos", summary.get("segment_count", "N/A")),
        ("Ritmo medio", f"{summary.get('speech_rate_wpm_mean')} ppm" if summary.get("speech_rate_wpm_mean") is not None else "N/A"),
        ("Pausas >= 1.5s", summary.get("pause_dramatic_count", "N/A")),
        ("Pitch mediano", f"{summary.get('global_pitch_median_hz')} Hz" if summary.get("global_pitch_median_hz") is not None else "N/A"),
        ("Alertas tecnicas", int(summary.get("low_confidence_segment_count", 0)) + int(summary.get("compression_alert_segment_count", 0)) + int(summary.get("noise_alert_segment_count", 0))),
    ]
    for column, (label, value) in zip(kpi_cols, metrics):
        with column:
            st.markdown(
                f"<div class='mini-kpi'><div class='label'>{label}</div><div class='value'>{value}</div></div>",
                unsafe_allow_html=True,
            )

    fig = build_intensity_timeline(segment_rows)
    if fig is not None:
        st.plotly_chart(fig, width="stretch")

    left, right = st.columns(2)
    with left:
        st.markdown("**Momentos destacados**")
        if audio_profile.get("salient_moments"):
            for item in audio_profile.get("salient_moments", []):
                reasons = ", ".join(item.get("reasons", []))
                st.markdown(
                    f"- **{item.get('time_label')}** | intensidad {item.get('intensity_index')} | "
                    f"{item.get('text_excerpt')}  \n  _Pistas: {reasons}_"
                )
        else:
            st.write("No se detectaron momentos destacados.")
    with right:
        st.markdown("**Alertas y cautelas**")
        for item in audio_profile.get("quality_warnings", []):
            st.markdown(f"- {item}")
        for item in audio_profile.get("cautions", []):
            st.markdown(f"- {item}")
        if summary.get("emphasis_words"):
            st.markdown("**Palabras enfatizadas por duracion**")
            st.write(", ".join(summary.get("emphasis_words", [])))

    if segment_export_rows:
        with st.expander("Tabla de segmentos y rasgos de audio", expanded=False):
            st.dataframe(pd.DataFrame(segment_export_rows), width="stretch", hide_index=True)


def build_technical_summary(status: str, error_message: str | None = None) -> dict[str, Any]:
    audio_telemetry = st.session_state.whisper_telemetry or {}
    relevance_telemetry = st.session_state.relevance_telemetry or {}
    analysis_telemetry = st.session_state.analysis_telemetry or {}
    stage_timings = st.session_state.pipeline_stage_timings or {}

    whisper_cost = _as_float(
        audio_telemetry.get("transcription", {}).get("estimated_cost_usd")
        if isinstance(audio_telemetry.get("transcription"), dict)
        else None
    ) or 0.0
    relevance_cost = _as_float(
        relevance_telemetry.get("pricing", {}).get("estimated_cost_usd")
        if isinstance(relevance_telemetry.get("pricing"), dict)
        else None
    ) or 0.0
    analysis_cost = _as_float(
        analysis_telemetry.get("pricing", {}).get("estimated_cost_usd")
        if isinstance(analysis_telemetry.get("pricing"), dict)
        else None
    ) or 0.0
    total_cost = whisper_cost + relevance_cost + analysis_cost

    def usage_for(item: dict[str, Any]) -> dict[str, int]:
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        return {
            "input_tokens": _as_int(usage.get("input_tokens"), 0),
            "cached_input_tokens": _as_int(usage.get("cached_input_tokens"), 0),
            "cache_write_tokens": _as_int(usage.get("cache_write_tokens"), 0),
            "output_tokens": _as_int(usage.get("output_tokens"), 0),
            "reasoning_tokens": _as_int(usage.get("reasoning_tokens"), 0),
            "total_tokens": _as_int(usage.get("total_tokens"), 0),
        }

    relevance_usage = usage_for(relevance_telemetry)
    analysis_usage = usage_for(analysis_telemetry)
    total_gpt_usage = {
        key: relevance_usage[key] + analysis_usage[key]
        for key in relevance_usage
    }

    summary = {
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "error_message": error_message,
        "run": {
            "started_at": st.session_state.run_started_at,
            "finished_at": st.session_state.run_finished_at,
            "processing_time_s": st.session_state.processing_time_s,
            "audio_hash": st.session_state.audio_hash,
            "prompt_profile": st.session_state.last_prompt_profile,
        },
        "models": {
            "transcription": OPENAI_TRANSCRIPTION_MODEL,
            "analysis": OPENAI_ANALYSIS_MODEL,
        },
        "audio": {
            "file_name": st.session_state.audio_file_name,
            "original_size_bytes": st.session_state.audio_original_size_bytes,
            "original_size_human": _format_bytes(st.session_state.audio_original_size_bytes),
            "api_audio_size_bytes": st.session_state.audio_api_size_bytes,
            "api_audio_size_human": _format_bytes(st.session_state.audio_api_size_bytes),
            "analysis_wav_size_bytes": st.session_state.audio_analysis_wav_size_bytes,
            "analysis_wav_size_human": _format_bytes(st.session_state.audio_analysis_wav_size_bytes),
            "api_audio_reduction_percent": audio_telemetry.get("api_audio_reduction_percent"),
            "duration_s": st.session_state.audio_duration_s,
            "duration_label": _seconds_to_label(st.session_state.audio_duration_s),
            "api_audio_specification": audio_telemetry.get("api_audio_format"),
            "analysis_wav_specification": audio_telemetry.get("analysis_wav_format"),
            "api_file_limit_bytes": OPENAI_AUDIO_FILE_LIMIT_BYTES,
            "api_file_limit_human": _format_bytes(OPENAI_AUDIO_FILE_LIMIT_BYTES),
            "theoretical_max_api_audio_duration_s": round(THEORETICAL_MAX_API_AUDIO_DURATION_S, 2),
            "theoretical_max_api_audio_duration_label": _seconds_to_label(THEORETICAL_MAX_API_AUDIO_DURATION_S),
        },
        "transcription": audio_telemetry.get("transcription", {}),
        "transcript": {
            "characters": len(st.session_state.transcript or ""),
            "words": st.session_state.word_count or 0,
            "segments": len(st.session_state.segment_rows or []),
        },
        "relevance_call": relevance_telemetry,
        "analysis_call": analysis_telemetry,
        "gpt_usage_total": total_gpt_usage,
        "timings_s": stage_timings,
        "costs_usd": {
            "whisper": round(whisper_cost, 8),
            "relevance": round(relevance_cost, 8),
            "political_analysis": round(analysis_cost, 8),
            "total": round(total_cost, 8),
        },
        "pricing_reference": {
            "date": PRICING_REFERENCE_DATE,
            "whisper_usd_per_minute": WHISPER_PRICE_PER_MINUTE_USD,
            "gpt_5_6_luna_short_context_usd_per_1m": {
                "input": GPT_SHORT_INPUT_PRICE_PER_1M,
                "cached_input": GPT_SHORT_CACHED_INPUT_PRICE_PER_1M,
                "cache_write": GPT_SHORT_CACHE_WRITE_PRICE_PER_1M,
                "output": GPT_SHORT_OUTPUT_PRICE_PER_1M,
            },
            "gpt_5_6_luna_long_context_usd_per_1m": {
                "input": GPT_LONG_INPUT_PRICE_PER_1M,
                "cached_input": GPT_LONG_CACHED_INPUT_PRICE_PER_1M,
                "cache_write": GPT_LONG_CACHE_WRITE_PRICE_PER_1M,
                "output": GPT_LONG_OUTPUT_PRICE_PER_1M,
            },
            "long_context_threshold_tokens": GPT_LONG_CONTEXT_THRESHOLD_TOKENS,
            "note": "Los costos son estimaciones calculadas con el uso devuelto por OpenAI y las tarifas configuradas.",
        },
        "environment": {
            "python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}",
            "streamlit": _package_version("streamlit"),
            "openai": _package_version("openai"),
            "librosa": _package_version("librosa"),
            "pydub": _package_version("pydub"),
            "ffmpeg_path": shutil.which("ffmpeg"),
        },
    }
    st.session_state.estimated_total_cost_usd = round(total_cost, 8)
    return summary


def technical_summary_to_text(summary: dict[str, Any]) -> str:
    audio = summary.get("audio", {})
    transcript = summary.get("transcript", {})
    transcription = summary.get("transcription", {})
    relevance = summary.get("relevance_call", {})
    analysis = summary.get("analysis_call", {})
    costs = summary.get("costs_usd", {})
    timings = summary.get("timings_s", {})
    pricing = summary.get("pricing_reference", {})

    def call_lines(title: str, call: dict[str, Any]) -> list[str]:
        usage = call.get("usage") if isinstance(call.get("usage"), dict) else {}
        price = call.get("pricing") if isinstance(call.get("pricing"), dict) else {}
        if not call:
            return [f"{title}: no ejecutado."]
        return [
            title,
            f"  Modelo solicitado: {call.get('model_requested', 'N/A')}",
            f"  Modelo devuelto: {call.get('model_returned', 'N/A')}",
            f"  Request ID: {call.get('request_id') or 'no disponible'}",
            f"  Caracteres system prompt: {call.get('system_prompt_characters', 0):,}",
            f"  Caracteres user prompt: {call.get('user_prompt_characters', 0):,}",
            f"  Caracteres input total: {call.get('input_characters_total', 0):,}",
            f"  Caracteres output visible: {call.get('output_visible_characters', 0):,}",
            f"  Tokens input: {_as_int(usage.get('input_tokens'), 0):,}",
            f"  Tokens input cacheados: {_as_int(usage.get('cached_input_tokens'), 0):,}",
            f"  Tokens escritos en cache: {_as_int(usage.get('cache_write_tokens'), 0):,}",
            f"  Tokens output: {_as_int(usage.get('output_tokens'), 0):,}",
            f"  Tokens de razonamiento incluidos en output: {_as_int(usage.get('reasoning_tokens'), 0):,}",
            f"  Tokens totales: {_as_int(usage.get('total_tokens'), 0):,}",
            f"  Fuente del conteo: {call.get('token_source', 'N/A')}",
            f"  Tier de precio: {price.get('context_tier', 'N/A')}",
            f"  Costo estimado: {_format_usd(_as_float(price.get('estimated_cost_usd')) or 0.0)}",
            f"  Tiempo API: {call.get('elapsed_s', 'N/A')} s",
        ]

    lines = [
        "RESUMEN TECNICO DE EJECUCION",
        "=" * 72,
        f"Generado: {summary.get('generated_at', 'N/A')}",
        f"Estado: {summary.get('status', 'N/A')}",
        f"Error: {summary.get('error_message') or 'Ninguno'}",
        f"Hash del audio: {summary.get('run', {}).get('audio_hash') or 'N/A'}",
        f"Perfil de prompt: {summary.get('run', {}).get('prompt_profile') or 'N/A'}",
        "",
        "MODELOS",
        f"  Transcripcion: {summary.get('models', {}).get('transcription', 'N/A')}",
        f"  Analisis: {summary.get('models', {}).get('analysis', 'N/A')}",
        "",
        "ARCHIVO Y PREPROCESAMIENTO",
        f"  Archivo original: {audio.get('file_name') or 'N/A'}",
        f"  Tamano original: {audio.get('original_size_human', 'N/A')} ({audio.get('original_size_bytes') or 0:,} bytes)",
        f"  M4A/AAC enviado a OpenAI: {audio.get('api_audio_size_human', 'N/A')} ({audio.get('api_audio_size_bytes') or 0:,} bytes)",
        f"  WAV local para Librosa: {audio.get('analysis_wav_size_human', 'N/A')} ({audio.get('analysis_wav_size_bytes') or 0:,} bytes)",
        f"  Reduccion del archivo enviado frente al original: {audio.get('api_audio_reduction_percent', 'N/A')}%",
        f"  Duracion: {audio.get('duration_label', 'N/A')} ({audio.get('duration_s') or 0} s)",
        f"  Limite configurado: {audio.get('api_file_limit_human', 'N/A')}",
        f"  Maximo teorico estimado del M4A/AAC: {audio.get('theoretical_max_api_audio_duration_label', 'N/A')}",
        "",
        "TRANSCRIPCION WHISPER",
        f"  Modelo: {transcription.get('model_returned') or transcription.get('model_requested') or 'N/A'}",
        f"  Request ID: {transcription.get('request_id') or 'no disponible'}",
        f"  Minutos facturables estimados: {transcription.get('billable_minutes', 0)}",
        f"  Tarifa: {_format_usd(_as_float(transcription.get('price_per_minute_usd')) or 0.0)} por minuto",
        f"  Costo estimado: {_format_usd(_as_float(transcription.get('estimated_cost_usd')) or 0.0)}",
        f"  Caracteres del prompt inicial: {transcription.get('initial_prompt_characters', 0):,}",
        f"  Tiempo API: {transcription.get('elapsed_s', 'N/A')} s",
        f"  Nota de uso: {transcription.get('usage_note', 'N/A')}",
        "",
        "TRANSCRIPCION RESULTANTE",
        f"  Caracteres: {_as_int(transcript.get('characters'), 0):,}",
        f"  Palabras: {_as_int(transcript.get('words'), 0):,}",
        f"  Segmentos: {_as_int(transcript.get('segments'), 0):,}",
        "",
    ]
    lines.extend(call_lines("LLAMADA 1 - FILTRO DE RELEVANCIA", relevance))
    lines.append("")
    lines.extend(call_lines("LLAMADA 2 - ANALISIS POLITICO", analysis))
    lines.extend([
        "",
        "COSTO TOTAL ESTIMADO",
        f"  Whisper: {_format_usd(_as_float(costs.get('whisper')) or 0.0)}",
        f"  Relevancia: {_format_usd(_as_float(costs.get('relevance')) or 0.0)}",
        f"  Analisis politico: {_format_usd(_as_float(costs.get('political_analysis')) or 0.0)}",
        f"  TOTAL: {_format_usd(_as_float(costs.get('total')) or 0.0)}",
        "",
        "TIEMPOS POR ETAPA",
    ])
    for name, value in timings.items():
        lines.append(f"  {name}: {value} s")
    lines.extend([
        "",
        "REFERENCIA DE PRECIOS",
        f"  Fecha de referencia: {pricing.get('date', 'N/A')}",
        f"  Umbral de contexto largo: {pricing.get('long_context_threshold_tokens', 0):,} tokens input",
        "  Los valores son estimaciones y pueden diferir de la facturacion final de OpenAI.",
    ])
    return "\n".join(lines)


def render_preflight_report(report: dict[str, Any]) -> None:
    with st.expander("Chequeo operativo", expanded=not report.get("ready", False)):
        for item in report.get("checks", []):
            icon = "✅" if item.get("ok") else ("❌" if item.get("critical") else "⚠️")
            st.markdown(f"{icon} **{item.get('name')}** — {item.get('detail')}")
        if report.get("prompt_diagnostics"):
            st.markdown("**Archivos de prompt descartados**")
            for diagnostic in report.get("prompt_diagnostics", []):
                st.warning(diagnostic)
        if report.get("project_doc_diagnostics"):
            st.markdown("**Documentos del proyecto con errores**")
            for diagnostic in report.get("project_doc_diagnostics", []):
                st.warning(diagnostic)
        versions = report.get("versions", {})
        st.caption(
            "Versiones: "
            f"Python {versions.get('python')} · Streamlit {versions.get('streamlit')} · "
            f"OpenAI {versions.get('openai')} · Librosa {versions.get('librosa')}"
        )


def render_technical_summary() -> None:
    summary = st.session_state.technical_summary
    if not summary:
        st.info("Todavia no hay un resumen tecnico. Ejecuta el pipeline para generarlo.")
        return

    audio = summary.get("audio", {})
    costs = summary.get("costs_usd", {})
    total_usage = summary.get("gpt_usage_total", {})
    kpi_cols = st.columns(4)
    kpis = [
        ("Audio original", audio.get("original_size_human", "N/A"), audio.get("file_name") or "Archivo cargado"),
        ("M4A enviado", audio.get("api_audio_size_human", "N/A"), f"{audio.get('api_audio_reduction_percent', 'N/A')}% menos vs. original"),
        ("Tokens GPT totales", f"{_as_int(total_usage.get('total_tokens'), 0):,}", "Relevancia + analisis"),
        ("Costo total estimado", _format_usd(_as_float(costs.get("total")) or 0.0), "Whisper + GPT"),
    ]
    for column, (label, value, note) in zip(kpi_cols, kpis):
        with column:
            render_kpi_card(label, value, note)

    st.subheader("Archivos y transcripcion")
    transcription = summary.get("transcription", {})
    st.dataframe(
        pd.DataFrame(
            [
                {"Elemento": "Audio original", "Tamano": audio.get("original_size_human"), "Detalle": audio.get("file_name")},
                {"Elemento": "M4A/AAC enviado a OpenAI", "Tamano": audio.get("api_audio_size_human"), "Detalle": str(audio.get("api_audio_specification"))},
                {"Elemento": "WAV local para Librosa", "Tamano": audio.get("analysis_wav_size_human"), "Detalle": str(audio.get("analysis_wav_specification"))},
                {"Elemento": "Whisper", "Tamano": "N/A", "Detalle": f"{transcription.get('billable_minutes', 0)} min · {_format_usd(_as_float(transcription.get('estimated_cost_usd')) or 0.0)}"},
            ]
        ),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Consumo de GPT-5.6 Luna")
    rows = []
    for label, call in [
        ("Filtro de relevancia", summary.get("relevance_call", {})),
        ("Analisis politico", summary.get("analysis_call", {})),
    ]:
        if not call:
            rows.append({"Etapa": label, "Estado": "No ejecutada"})
            continue
        usage = call.get("usage", {})
        pricing = call.get("pricing", {})
        rows.append(
            {
                "Etapa": label,
                "Modelo": call.get("model_returned") or call.get("model_requested"),
                "Caracteres input": call.get("input_characters_total", 0),
                "Tokens input": usage.get("input_tokens", 0),
                "Tokens cacheados": usage.get("cached_input_tokens", 0),
                "Cache writes": usage.get("cache_write_tokens", 0),
                "Tokens output": usage.get("output_tokens", 0),
                "Tokens razonamiento": usage.get("reasoning_tokens", 0),
                "Tokens totales": usage.get("total_tokens", 0),
                "Contexto": pricing.get("context_tier"),
                "Costo estimado USD": pricing.get("estimated_cost_usd", 0),
                "Tiempo API s": call.get("elapsed_s"),
                "Request ID": call.get("request_id") or "no disponible",
            }
        )
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    st.subheader("Tiempos por etapa")
    timing_rows = [
        {"Etapa": key, "Segundos": value}
        for key, value in summary.get("timings_s", {}).items()
    ]
    if timing_rows:
        st.dataframe(pd.DataFrame(timing_rows), width="stretch", hide_index=True)

    st.caption(
        f"Precios de referencia: {PRICING_REFERENCE_DATE}. "
        "Los costos son estimaciones; la facturacion final de OpenAI es la fuente definitiva."
    )
    st.download_button(
        "Descargar resumen tecnico (.txt)",
        data=st.session_state.technical_summary_text or technical_summary_to_text(summary),
        file_name=f"resumen_tecnico_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
        mime="text/plain",
        width="stretch",
        key="technical_summary_download_tab",
    )
    with st.expander("Resumen tecnico estructurado"):
        st.json(summary, expanded=False)


def render_analysis_results() -> None:
    transcript = st.session_state.transcript
    relevance = st.session_state.relevance
    analysis = st.session_state.analysis
    audio_profile = st.session_state.audio_profile
    segment_rows = st.session_state.segment_rows or []
    segment_export_rows = st.session_state.segment_export_rows or []

    if not transcript or not relevance:
        st.info("Todavia no hay resultados. Ejecuta el pipeline para ver el analisis.")
        return

    metric_cols = st.columns(4)
    kpis = [
        ("Duracion audio", f"{st.session_state.audio_duration_s or 0:.1f}s", "Audio procesado"),
        ("Palabras", f"{st.session_state.word_count or 0}", "Transcripcion base"),
        ("Densidad politica", f"{relevance.get('density_score', 0)}/100", "Filtro de relevancia"),
        ("Tiempo pipeline", f"{st.session_state.processing_time_s or 0:.1f}s", "Ejecucion total"),
    ]
    for column, (label, value, note) in zip(metric_cols, kpis):
        with column:
            render_kpi_card(label, value, note)

    badge = "badge-good" if relevance.get("relevant") else "badge-bad"
    label = "RELEVANTE" if relevance.get("relevant") else "NO RELEVANTE"
    st.markdown(f'<span class="{badge}">{label}</span>', unsafe_allow_html=True)
    st.write(relevance.get("reason", ""))

    c1, c2 = st.columns(2)
    with c1:
        render_list("Criterios cumplidos", relevance.get("criteria_met", []))
    with c2:
        render_list("Contexto faltante", relevance.get("missing_context_for_analysis", []))

    inner_tabs = st.tabs(["Coyuntura", "Disputa de sentidos", "Evidencia de audio y trazabilidad"])

    with inner_tabs[0]:
        if not relevance.get("relevant"):
            st.warning("El pipeline se detuvo despues del filtro de relevancia porque la transcripcion no ofrece suficiente densidad politica.")
            with st.expander("Transcripcion para triangulacion"):
                st.text_area("Transcripcion de triangulacion", transcript, height=280, disabled=True, key="triangulation_textarea")
        elif not analysis:
            st.warning("La transcripcion paso el filtro, pero no se genero el analisis estrategico.")
        else:
            st.subheader("Resumen ejecutivo")
            st.write(analysis.get("summary", ""))

            st.markdown("---")
            st.subheader("Tiempo y estructura/coyuntura")
            tm_col, st_col = st.columns(2)
            time_block = analysis.get("time", {})
            structure = analysis.get("structure_coyuntura", {})
            with tm_col:
                st.markdown("**Tiempo**")
                st.write(f"Larga duracion: {time_block.get('long_duration', {}).get('description', 'no identificable')}")
                st.write(f"Mediana duracion: {time_block.get('medium_duration', {}).get('description', 'no identificable')}")
                st.write(f"Corta duracion: {time_block.get('short_duration', {}).get('description', 'no identificable')}")
            with st_col:
                st.markdown("**Estructura/coyuntura**")
                st.write(f"Economica: {structure.get('economic', {}).get('description', 'no identificable')}")
                st.write(f"Politica: {structure.get('political', {}).get('description', 'no identificable')}")
                st.write(f"Social: {structure.get('social', {}).get('description', 'no identificable')}")

            ### AJUSTE EDISON — AJUSTADO: eventos usan .get("description") (antes
            ### eran strings planos); escenarios sin "Mediatico", con "Publico
            ### institucional/social" y "Privado" en vez de "Institucional" generico. ###
            st.markdown("---")
            st.subheader("Acontecimientos y escenarios")
            ev_col, sc_col = st.columns(2)
            events = analysis.get("events", {})
            scenarios = analysis.get("scenarios", {})
            with ev_col:
                st.markdown("**Acontecimiento desencadenante**")
                st.write(events.get("trigger_event", {}).get("description", "no identificable"))
                render_list("Eventos derivados", [item.get("description", "no identificable") for item in events.get("derived_events", [])])
                render_list("Tendencias de fondo", [item.get("description", "no identificable") for item in events.get("background_trends", [])])
            with sc_col:
                st.markdown("**Escenarios**")
                st.write(f"Publico institucional: {scenarios.get('public_institutional', {}).get('description', 'no identificable')}")
                st.write(f"Publico social: {scenarios.get('public_social', {}).get('description', 'no identificable')}")
                st.write(f"Privado: {scenarios.get('private', {}).get('description', 'no identificable')}")
                st.write(f"Internacional: {scenarios.get('international', {}).get('description', 'no identificable')}")

            st.markdown("---")
            st.subheader("Mapa de actores")
            render_actor_table(analysis.get("actors", []))

            st.markdown("---")
            st.subheader("Recomendaciones compartidas")
            shared = analysis.get("shared_recommendations", [])
            if not shared:
                st.write("No se identificaron relaciones de cooperacion que ameriten una recomendacion compartida.")
            else:
                for idx, item in enumerate(shared, start=1):
                    actors_label = ", ".join(item.get("for_actors", [])) or "no identificable"
                    st.markdown(f"**{idx}. {actors_label}**")
                    st.write(item.get("suggestion", "no identificable"))
                    if item.get("based_on"):
                        st.caption(f"Basado en: {item.get('based_on')}")

            st.markdown("---")
            st.subheader("Limitaciones")
            st.write(analysis.get("limitations", "No se especificaron limitaciones."))

    with inner_tabs[1]:
        if not relevance.get("relevant") or not analysis:
            st.info("La disputa de sentidos aparecera aqui cuando el audio pase el filtro y se complete el analisis.")
        else:
            dispute = analysis.get("dispute_of_meaning", {})
            frontier = dispute.get("friend_enemy_frontier", {})
            e1, e2 = st.columns(2)
            with e1:
                st.markdown("**Construccion del nosotros**")
                st.write(frontier.get("us_construction", "no identificable"))
            with e2:
                st.markdown("**Construccion del ellos**")
                st.write(frontier.get("them_construction", "no identificable"))
            if frontier.get("evidence"):
                st.caption(f'Evidencia: "{frontier.get("evidence")}"')

            resignifications = dispute.get("resignification_of_concepts", [])
            if resignifications:
                st.markdown("**Resignificacion de conceptos**")
                rows = []
                for item in resignifications:
                    readings = "; ".join(
                        f"{r.get('actor', 'no identificable')} = {r.get('meaning', 'no identificable')}"
                        for r in item.get("readings", [])
                    )
                    rows.append({"Concepto": item.get("concept", "no identificable"), "Lecturas en disputa": readings})
                st.dataframe(rows, width="stretch", hide_index=True)
            else:
                st.write("No se identifico resignificacion de conceptos.")

    with inner_tabs[2]:
        render_audio_profile(audio_profile, segment_rows, segment_export_rows)
        with st.expander("Transcripcion completa", expanded=False):
            st.text_area("Transcripcion completa", transcript, height=320, disabled=True, key="full_transcript_textarea")
        with st.expander("JSON estructurado", expanded=False):
            st.json(
                {
                    "relevance": st.session_state.relevance,
                    "analysis": st.session_state.analysis,
                    "audio_profile": st.session_state.audio_profile,
                },
                expanded=False,
            )
        with st.expander("Depuracion tecnica", expanded=False):
            st.markdown(f"**Perfil usado:** {st.session_state.last_prompt_profile}")
            st.markdown(f"**Motor de transcripcion:** {st.session_state.last_transcription_engine}")
            st.markdown(f"**Motor LLM:** {st.session_state.last_llm_engine}")
            st.markdown("**Respuesta cruda del filtro de relevancia**")
            st.code(st.session_state.raw_relevance_response or "", language="json")
            st.markdown("**Respuesta cruda del analisis**")
            st.code(st.session_state.raw_analysis_response or "", language="json")


def render_project_docs(project_docs: list[ProjectDoc]) -> None:
    if not project_docs:
        st.info("No hay documentos teoricos cargados en project_docs/.")
        return

    default_doc_id = next((doc.doc_id for doc in project_docs if doc.is_default), project_docs[0].doc_id)
    current_doc_id = st.session_state.selected_project_doc_id or default_doc_id

    options = {doc.title: doc for doc in project_docs}
    selected_title_by_id = next(
        (title for title, doc in options.items() if doc.doc_id == current_doc_id),
        next(iter(options.keys())),
    )
    selected_title = st.selectbox(
        "Documento del proyecto",
        options=list(options.keys()),
        index=list(options.keys()).index(selected_title_by_id),
        help="Cada pagina vive en project_docs/ y puede editarse sin tocar el codigo.",
    )
    selected_doc = options[selected_title]
    st.session_state.selected_project_doc_id = selected_doc.doc_id

    st.markdown(
        f"<div class='soft-card'><h3>{selected_doc.title}</h3><p class='soft-muted'>{selected_doc.description}</p>"
        f"<span class='project-chip'>{selected_doc.source_path.name}</span></div>",
        unsafe_allow_html=True,
    )
    st.markdown(selected_doc.body)


def main() -> None:
    init_session_state()

    prompt_profiles, prompt_diagnostics = load_prompt_profiles(PROMPTS_DIR)
    project_docs, project_doc_diagnostics = load_project_docs(PROJECT_DOCS_DIR)
    preflight_report = build_preflight_report(
        prompt_profiles=prompt_profiles,
        prompt_diagnostics=prompt_diagnostics,
        project_doc_diagnostics=project_doc_diagnostics,
    )

    st.markdown(
        f"""
        <div class="app-hero">
            <h1>{APP_TITLE}</h1>
            <p>{APP_DESCRIPTION}</p>
            <p class="soft-muted">{APP_SUBTITLE}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not prompt_profiles:
        st.error(
            "No hay perfiles de prompt validos. Revisa src/system_prompts/ antes de ejecutar el pipeline."
        )
        render_preflight_report(preflight_report)
        st.stop()

    default_profile_index = next((i for i, item in enumerate(prompt_profiles) if item.is_default), 0)
    transcription_engine = "OpenAI Whisper API"
    llm_engine = "OpenAI GPT-5.6-luna"

    with st.sidebar:
        st.header("Configuracion")
        st.markdown(f"**Transcripcion:** `{OPENAI_TRANSCRIPTION_MODEL}`")
        st.caption(
            f"M4A/AAC {OPENAI_AUDIO_BITRATE_KBPS} kbps · mono {AUDIO_SAMPLE_RATE_HZ:,} Hz · "
            f"limite {_format_bytes(OPENAI_AUDIO_FILE_LIMIT_BYTES)} · "
            f"maximo teorico estimado {_seconds_to_label(THEORETICAL_MAX_API_AUDIO_DURATION_S)}"
        )
        st.markdown(f"**Analisis:** `{OPENAI_ANALYSIS_MODEL}`")
        st.caption(
            f"Entrada corta {_format_usd(GPT_SHORT_INPUT_PRICE_PER_1M, 2)}/1M · "
            f"salida {_format_usd(GPT_SHORT_OUTPUT_PRICE_PER_1M, 2)}/1M"
        )
        selected_profile = st.selectbox(
            "Perfil de system prompt",
            options=prompt_profiles,
            index=default_profile_index,
            format_func=lambda profile: profile.name,
            help="Cada perfil vive en src/system_prompts/ y puede editarse sin tocar app.py.",
        )
        st.caption(selected_profile.description)
        st.markdown(f"Archivo activo: `{selected_profile.source_path.name}`")
        with st.expander("Ver prompt activo"):
            st.markdown("**Relevance prompt**")
            st.code(selected_profile.relevance_prompt, language="markdown")
            st.markdown("**Analysis prompt**")
            st.code(selected_profile.analysis_prompt, language="markdown")
        render_preflight_report(preflight_report)

    tabs = st.tabs(["Pipeline", "Resultados", "Resumen tecnico", "Exportacion", "Proyecto"])

    with tabs[0]:
        left, right = st.columns([1.5, 1])
        audio_preview: dict[str, Any] | None = None
        preview_error: str | None = None

        with left:
            uploaded_file = st.file_uploader(
                "Audio",
                type=["mp3", "wav", "m4a", "flac", "ogg"],
                help=(
                    "Carga un archivo MP3, WAV, M4A, FLAC u OGG. El sistema genera un M4A/AAC "
                    f"mono de {AUDIO_SAMPLE_RATE_HZ // 1000} kHz y {OPENAI_AUDIO_BITRATE_KBPS} kbps para OpenAI, "
                    "ademas de un WAV temporal separado para Librosa."
                ),
            )

            if uploaded_file is not None:
                try:
                    preview_bytes = uploaded_file.getvalue()
                    audio_preview = inspect_audio_input(preview_bytes, uploaded_file.name)
                    st.session_state.audio_preview = audio_preview
                    preview_cols = st.columns(4)
                    preview_metrics = [
                        ("Archivo original", _format_bytes(audio_preview["original_size_bytes"])),
                        ("Duracion", _seconds_to_label(audio_preview["duration_s"])),
                        ("M4A estimado", _format_bytes(audio_preview["estimated_api_audio_size_bytes"])),
                        ("Limite API", _format_bytes(audio_preview["api_file_limit_bytes"])),
                    ]
                    for column, (label, value) in zip(preview_cols, preview_metrics):
                        with column:
                            st.metric(label, value)
                    st.caption(
                        f"Formato detectado: {audio_preview.get('format', 'desconocido')} · "
                        f"Maximo teorico estimado del M4A/AAC: {_seconds_to_label(THEORETICAL_MAX_API_AUDIO_DURATION_S)}. "
                        "La cifra es una estimacion previa; el resumen tecnico registra el M4A real enviado y el WAV local."
                    )
                    if audio_preview.get("estimated_api_audio_exceeds_limit"):
                        st.error(
                            "El M4A/AAC estimado excede el limite configurado. Este MVP todavia no fragmenta "
                            "audios automaticamente; usa un audio mas corto antes de ejecutar."
                        )
                except Exception as exc:
                    preview_error = str(exc)
                    st.error(preview_error)

            initial_context = st.text_area(
                "Contexto inicial del audio (requerido)",
                placeholder=(
                    "Describe fecha, evento, actores, lugar y antecedentes relevantes. "
                    "Ej.: instalacion del Congreso, reforma laboral, Colombia, partidos presentes y fecha."
                ),
                height=110,
                help="El contexto se envia a Whisper como apoyo lexico y al LLM como marco situacional declarado.",
            )

            run_disabled = (
                not preflight_report.get("ready", False)
                or preview_error is not None
                or bool(audio_preview and audio_preview.get("estimated_api_audio_exceeds_limit"))
            )
            run_pipeline = st.button(
                "Ejecutar pipeline",
                type="primary",
                width="stretch",
                disabled=run_disabled,
                help=(
                    "Corrige primero los chequeos operativos o el tamano del audio."
                    if run_disabled
                    else "Inicia transcripcion, perfil de audio, filtro, analisis y exportacion."
                ),
            )

        with right:
            st.markdown("<div class='soft-card'><h4>Valor del artefacto</h4>", unsafe_allow_html=True)
            st.markdown(
                "- Transcripcion temporal y auditable.\n"
                "- Perfil exploratorio de pausas, ritmo, pitch y energia.\n"
                "- Filtro de relevancia politica.\n"
                "- Analisis estrategico estructurado.\n"
                "- Trazabilidad de caracteres, tokens, tiempos y costos.\n"
                "- Exportacion de PDF, TXT, JSON y CSV."
            )
            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("<div class='soft-card'><h4>Modelos y precios configurados</h4>", unsafe_allow_html=True)
            st.markdown(
                f"<span class='project-chip'>{OPENAI_TRANSCRIPTION_MODEL}</span>"
                f"<span class='project-chip'>{OPENAI_ANALYSIS_MODEL}</span>",
                unsafe_allow_html=True,
            )
            st.caption(
                f"Whisper: {_format_usd(WHISPER_PRICE_PER_MINUTE_USD, 3)}/min · "
                f"GPT corto: {_format_usd(GPT_SHORT_INPUT_PRICE_PER_1M, 2)} input y "
                f"{_format_usd(GPT_SHORT_OUTPUT_PRICE_PER_1M, 2)} output por 1M tokens · "
                f"referencia {PRICING_REFERENCE_DATE}."
            )
            st.markdown("</div>", unsafe_allow_html=True)

            if st.session_state.audio_profile:
                summary = st.session_state.audio_profile.get("summary", {})
                st.markdown("<div class='soft-card'><h4>Ultimo perfil de audio</h4>", unsafe_allow_html=True)
                chips = [
                    f"ritmo {summary.get('speech_rate_wpm_mean')} ppm" if summary.get("speech_rate_wpm_mean") is not None else "",
                    f"pausas {summary.get('pause_dramatic_count')}" if summary.get("pause_dramatic_count") is not None else "",
                    f"pitch {summary.get('global_pitch_median_hz')} Hz" if summary.get("global_pitch_median_hz") is not None else "",
                ]
                st.markdown(
                    " ".join(f"<span class='project-chip'>{item}</span>" for item in chips if item),
                    unsafe_allow_html=True,
                )
                st.markdown("</div>", unsafe_allow_html=True)

        if run_pipeline:
            if uploaded_file is None:
                st.warning("Debes cargar un archivo MP3, WAV, M4A, FLAC u OGG antes de ejecutar el pipeline.")
            elif not initial_context or not initial_context.strip():
                st.error("El contexto inicial es obligatorio. Describe el marco situacional antes de procesar.")
            elif not preflight_report.get("ready", False):
                st.error("El entorno no supera los chequeos operativos. Revisa el panel lateral.")
            else:
                reset_keys = [
                    "transcript",
                    "transcription_payload",
                    "relevance",
                    "analysis",
                    "audio_profile",
                    "segment_rows",
                    "segment_export_rows",
                    "segment_csv_bytes",
                    "audio_brief_text",
                    "audio_llm_payload",
                    "pdf_bytes",
                    "raw_relevance_response",
                    "raw_analysis_response",
                    "processing_time_s",
                    "audio_duration_s",
                    "word_count",
                    "audio_hash",
                    "audio_file_name",
                    "audio_original_size_bytes",
                    "audio_api_size_bytes",
                    "audio_analysis_wav_size_bytes",
                    "audio_api_format",
                    "whisper_telemetry",
                    "relevance_telemetry",
                    "analysis_telemetry",
                    "pipeline_stage_timings",
                    "technical_summary",
                    "technical_summary_text",
                    "estimated_total_cost_usd",
                    "last_run_status",
                    "last_error_message",
                    "run_started_at",
                    "run_finished_at",
                ]
                for key in reset_keys:
                    st.session_state[key] = None

                st.session_state.last_prompt_profile = selected_profile.name
                st.session_state.last_llm_engine = OPENAI_ANALYSIS_MODEL
                st.session_state.last_transcription_engine = OPENAI_TRANSCRIPTION_MODEL
                st.session_state.run_started_at = datetime.now().isoformat()
                started_perf = time.perf_counter()
                stage_timings: dict[str, float] = {}
                execution_status = "running"
                error_message: str | None = None

                with st.status("Procesando artefacto DSRM...", expanded=True) as status:
                    try:
                        audio_bytes = uploaded_file.getvalue()
                        st.session_state.audio_file_name = uploaded_file.name
                        st.session_state.audio_original_size_bytes = len(audio_bytes)
                        st.session_state.audio_hash = hashlib.sha256(audio_bytes).hexdigest()[:16]

                        st.write(
                            f"1. Optimizando {_format_bytes(len(audio_bytes))} a M4A/AAC y ejecutando "
                            f"{OPENAI_TRANSCRIPTION_MODEL}..."
                        )
                        (
                            transcription_payload,
                            transcript,
                            duration_s,
                            segment_rows,
                            audio_profile,
                            segment_export_rows,
                            audio_brief_text,
                            audio_llm_payload,
                            audio_telemetry,
                        ) = process_audio_pipeline(
                            audio_bytes=audio_bytes,
                            engine=transcription_engine,
                            initial_prompt=initial_context.strip(),
                            file_name=uploaded_file.name,
                        )
                        st.session_state.transcription_payload = transcription_payload
                        st.session_state.transcript = transcript
                        st.session_state.audio_duration_s = duration_s
                        st.session_state.segment_rows = segment_rows
                        st.session_state.audio_profile = audio_profile
                        st.session_state.segment_export_rows = segment_export_rows
                        st.session_state.audio_brief_text = audio_brief_text
                        st.session_state.audio_llm_payload = audio_llm_payload
                        st.session_state.word_count = len(transcript.split())
                        st.session_state.segment_csv_bytes = (
                            pd.DataFrame(segment_export_rows).to_csv(index=False).encode("utf-8")
                        )
                        st.session_state.whisper_telemetry = audio_telemetry
                        st.session_state.audio_api_size_bytes = audio_telemetry.get("api_audio_size_bytes")
                        st.session_state.audio_analysis_wav_size_bytes = audio_telemetry.get("analysis_wav_size_bytes")
                        st.session_state.audio_api_format = audio_telemetry.get("api_audio_format")
                        stage_timings.update(audio_telemetry.get("timings_s", {}))

                        st.write(
                            "2. Audio optimizado y perfil de Librosa construidos. "
                            f"M4A enviado: {_format_bytes(st.session_state.audio_api_size_bytes)} · "
                            f"WAV local: {_format_bytes(st.session_state.audio_analysis_wav_size_bytes)}."
                        )
                        st.write(audio_brief_text)

                        st.write(f"3. Ejecutando filtro de relevancia con {OPENAI_ANALYSIS_MODEL}...")
                        relevance, raw_relevance, relevance_telemetry = run_relevance_filter(
                            transcript=transcript,
                            initial_context=initial_context.strip(),
                            audio_llm_payload=audio_llm_payload,
                            llm_engine=llm_engine,
                            relevance_prompt=selected_profile.relevance_prompt,
                        )
                        st.session_state.relevance = relevance
                        st.session_state.raw_relevance_response = raw_relevance
                        st.session_state.relevance_telemetry = relevance_telemetry
                        stage_timings["relevance_api"] = relevance_telemetry.get("elapsed_s", 0.0)

                        if relevance.get("relevant"):
                            st.write(f"4. Ejecutando analisis estrategico con {OPENAI_ANALYSIS_MODEL}...")
                            analysis, raw_analysis, analysis_telemetry = run_strategic_analysis(
                                transcript=transcript,
                                initial_context=initial_context.strip(),
                                audio_llm_payload=audio_llm_payload,
                                llm_engine=llm_engine,
                                analysis_prompt=selected_profile.analysis_prompt,
                                prompt_profile_name=selected_profile.name,
                            )
                            st.session_state.analysis = analysis
                            st.session_state.raw_analysis_response = raw_analysis
                            st.session_state.analysis_telemetry = analysis_telemetry
                            stage_timings["political_analysis_api"] = analysis_telemetry.get("elapsed_s", 0.0)

                            st.write("5. Generando PDF...")
                            pdf_started = time.perf_counter()
                            st.session_state.pdf_bytes = build_pdf(
                                analysis=analysis,
                                relevance=relevance,
                                transcript=transcript,
                                prompt_profile_name=selected_profile.name,
                                initial_context=initial_context.strip(),
                                audio_profile=audio_profile,
                            )
                            stage_timings["pdf_generation"] = round(time.perf_counter() - pdf_started, 3)
                            execution_status = "completed"
                        else:
                            st.session_state.analysis = None
                            st.session_state.analysis_telemetry = None
                            st.session_state.pdf_bytes = None
                            execution_status = "completed_after_relevance_filter"
                            st.write(
                                "4. El pipeline se detuvo de forma controlada: la transcripcion no supero "
                                "el umbral de densidad politica. No se cobro la segunda llamada de analisis."
                            )

                        status.update(
                            label="Pipeline completado",
                            state="complete",
                            expanded=False,
                        )
                    except Exception as exc:
                        execution_status = "error"
                        error_message = str(exc)
                        st.session_state.last_error_message = error_message
                        status.update(label="Error en el pipeline", state="error", expanded=True)
                        st.error(error_message)
                        with st.expander("Detalle tecnico", expanded=False):
                            st.code(traceback.format_exc())
                    finally:
                        total_elapsed = round(time.perf_counter() - started_perf, 3)
                        stage_timings["pipeline_total"] = total_elapsed
                        st.session_state.pipeline_stage_timings = stage_timings
                        st.session_state.processing_time_s = total_elapsed
                        st.session_state.run_finished_at = datetime.now().isoformat()
                        st.session_state.last_run_status = execution_status
                        summary = build_technical_summary(execution_status, error_message)
                        st.session_state.technical_summary = summary
                        st.session_state.technical_summary_text = technical_summary_to_text(summary)

    with tabs[1]:
        render_analysis_results()

    with tabs[2]:
        render_technical_summary()

    with tabs[3]:
        export_cols = st.columns(4)
        with export_cols[0]:
            st.download_button(
                "Descargar transcripcion (.txt)",
                data=st.session_state.transcript or "",
                file_name=f"transcripcion_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
                mime="text/plain",
                width="stretch",
                disabled=not bool(st.session_state.transcript),
                key="export_transcript_download",
            )
        with export_cols[1]:
            st.download_button(
                "Descargar informe (.pdf)",
                data=st.session_state.pdf_bytes or b"",
                file_name=f"informe_coyuntura_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf",
                mime="application/pdf",
                width="stretch",
                disabled=not bool(st.session_state.pdf_bytes),
                key="export_pdf_download",
            )
        with export_cols[2]:
            st.download_button(
                "Descargar segmentos (.csv)",
                data=st.session_state.segment_csv_bytes or b"",
                file_name=f"segmentos_audio_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv",
                width="stretch",
                disabled=not bool(st.session_state.segment_csv_bytes),
                key="export_segments_download",
            )
        with export_cols[3]:
            json_payload = json.dumps(
                {
                    "meta": {
                        "exported_at": datetime.now().isoformat(),
                        "prompt_profile": st.session_state.last_prompt_profile,
                        "transcription_model": OPENAI_TRANSCRIPTION_MODEL,
                        "analysis_model": OPENAI_ANALYSIS_MODEL,
                        "audio_hash": st.session_state.audio_hash,
                        "audio_duration_s": st.session_state.audio_duration_s,
                        "word_count": st.session_state.word_count,
                    },
                    "technical_summary": st.session_state.technical_summary,
                    "audio_profile": st.session_state.audio_profile,
                    "segments": st.session_state.segment_export_rows,
                    "relevance": st.session_state.relevance,
                    "analysis": st.session_state.analysis,
                    "transcript": st.session_state.transcript,
                },
                ensure_ascii=False,
                indent=2,
            )
            st.download_button(
                "Descargar JSON",
                data=json_payload,
                file_name=f"analisis_coyuntura_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                mime="application/json",
                width="stretch",
                disabled=not bool(st.session_state.relevance),
                key="export_json_download",
            )

        st.write("")
        st.download_button(
            "Descargar resumen tecnico (.txt)",
            data=st.session_state.technical_summary_text or "",
            file_name=f"resumen_tecnico_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            mime="text/plain",
            width="stretch",
            disabled=not bool(st.session_state.technical_summary_text),
            key="export_technical_summary_download",
        )
        st.markdown(
            "<div class='soft-card'><h4>Exportaciones auditables</h4>"
            "<p class='soft-muted'>El JSON incorpora el resumen tecnico completo: tamanos de archivo, "
            "modelos, caracteres, tokens, tiempos, request IDs y costos estimados. El TXT tecnico permite "
            "archivar esa trazabilidad por separado.</p></div>",
            unsafe_allow_html=True,
        )

    with tabs[4]:
        render_project_docs(project_docs)


if __name__ == "__main__":
    main()