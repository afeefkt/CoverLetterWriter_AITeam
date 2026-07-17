"""
============================================================
  COVER LETTER CREW
  CrewAI-powered — generates 2 English cover letter variants per job

  CLI usage:
    python cover_letter_crew.py

  Streamlit usage:
    streamlit run app.py

  Requirements:
    - DEEPSEEK_API_KEY set in .env  (cheapest option)
    - OR Ollama running locally with qwen3.5:9b (free)
============================================================
"""

import os
import re
import sys
import time
import json as _json
import subprocess
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Disable crewai's OpenTelemetry phone-home before importing it — outbound HTTPS
# is unreliable on some machines and each failed export blocks for seconds.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")

from crewai import Agent, Task, Crew, LLM
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

load_dotenv()

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL    = "qwen3.5:9b"

# Context window for local Ollama models. Ollama's own default (~2-4k) silently
# truncates the long task prompts, which wrecks output quality on local models.
OLLAMA_NUM_CTX  = int(os.getenv("OLLAMA_NUM_CTX", "16384"))


def _strip_think(text: str) -> str:
    """Remove qwen3-style <think>...</think> reasoning blocks from LLM output.

    An unclosed <think> means generation was cut off mid-reasoning — everything
    from there on is reasoning, not answer, so it is dropped too.
    """
    if not text:
        return text or ""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<think>.*\Z', '', text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _no_think_suffix(llm_config: dict | None) -> str:
    """Qwen3 soft switch: appending /no_think to a prompt disables thinking mode.
    qwen3.5 REMOVED this switch (it ignores it and reasons about the stray token),
    so it only applies to qwen3 tags. For the whole qwen3 family, thinking is also
    disabled at the API level via reasoning_effort="none" (see _ollama_extra_body);
    _strip_think() on every output path remains the last line of defense."""
    if not llm_config:
        return ""
    model = str(llm_config.get("model", "")).lower()
    if llm_config.get("backend") == "ollama" and "qwen3" in model and "qwen3.5" not in model:
        return " /no_think"
    return ""


def _ollama_extra_body(model: str) -> dict:
    """Raw JSON body fields for Ollama's OpenAI-compatible endpoint.

    - options.num_ctx: raise Ollama's small default context window (its ~2-4k
      default silently truncates this pipeline's long prompts).
    - reasoning_effort "none": disables thinking for qwen3-family models
      (qwen3, qwen3.5). Without it, thinking burns the max_tokens budget in the
      separate `reasoning` field and `content` comes back empty. Verified
      working against Ollama 0.31 (unknown fields are ignored by older versions).
    """
    body: dict = {"options": {"num_ctx": OLLAMA_NUM_CTX}}
    if "qwen3" in model.lower():
        body["reasoning_effort"] = "none"
    return body


def _ensure_ctx_model(model: str, base_url: str = OLLAMA_BASE_URL) -> str:
    """Return the name of a derived model with num_ctx baked in, creating it on
    the Ollama server if needed (e.g. 'qwen3.5:9b' -> 'qwen3.5:9b-ctx16384').

    Ollama's OpenAI-compatible /v1 endpoint IGNORES per-request `options`
    (verified on 0.31: the model loads with the ~4k default and long prompts get
    silently truncated), so the context size must live in the model itself.
    The derived model is metadata-only — it shares the base weights on disk.
    Falls back to the base model name if creation fails.
    """
    if OLLAMA_NUM_CTX <= 0:
        return model  # escape hatch: OLLAMA_NUM_CTX=0 disables the derived model
    ctx_name = f"{model}-ctx{OLLAMA_NUM_CTX}"
    if _model_is_pulled(ctx_name, base_url):
        return ctx_name
    try:
        payload = _json.dumps({
            "model": ctx_name,
            "from": model,
            "parameters": {"num_ctx": OLLAMA_NUM_CTX},
            "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/api/create", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            status = _json.loads(resp.read()).get("status", "")
        if status == "success" and _model_is_pulled(ctx_name, base_url):
            return ctx_name
    except Exception as e:
        if os.environ.get("DEBUG"):
            print(f"[DEBUG] _ensure_ctx_model failed: {e}")
    print(f"WARNING: could not create '{ctx_name}' — running with Ollama's "
          f"default context window; long prompts may be truncated.")
    return model


def _model_size_b(model: str) -> float | None:
    """Parse the parameter count (in billions) from a model tag like 'qwen3:8b'."""
    m = re.search(r'(\d+(?:\.\d+)?)\s*b\b', model.lower())
    return float(m.group(1)) if m else None


def _is_small_local_model(llm_config: dict | None) -> bool:
    """True for local Ollama models of ~9B or less — these get compact prompts
    (covers the qwen3:8b / qwen3.5:9b default class; 14B+ get full prompts)."""
    if not llm_config or llm_config.get("backend") != "ollama":
        return False
    size = _model_size_b(str(llm_config.get("model", "")))
    return size is None or size <= 9


def _is_localhost(base_url: str) -> bool:
    from urllib.parse import urlparse
    host = urlparse(base_url).hostname or ""
    return host in ("localhost", "127.0.0.1", "0.0.0.0", "::1")


def _extract_name_from_profile(text: str) -> str:
    """Multi-strategy regex name extraction — no LLM cost.
    Name parts are separated by [ \\t] (not \\s) so a labelled name never
    swallows the following line ("Name: Jane Smith\\nEmbedded Engineer")."""
    _part = r"[A-ZÀ-Þ][a-zA-ZÀ-ÿ'’\-]*"
    m = re.search(rf'(?i)(?:full\s+)?name\s*[:\-][ \t]*({_part}(?:[ \t]{_part}){{1,3}})', text)
    if m:
        return m.group(1).strip()
    for line in text.strip().splitlines()[:15]:
        line = line.strip()
        if 4 < len(line) < 50 and re.match(rf'^{_part}([ \t]{_part}){{1,3}}$', line):
            return line
    return ""


def _ollama_is_running(base_url: str = OLLAMA_BASE_URL) -> bool:
    try:
        urllib.request.urlopen(f"{base_url}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def _start_ollama(fatal: bool = True, base_url: str = OLLAMA_BASE_URL):
    """Start Ollama in the background and wait until it responds.
    If fatal=False, raises RuntimeError instead of sys.exit.
    Only attempts to launch a server for localhost URLs — a remote Ollama
    host cannot be started from this machine."""
    if not _is_localhost(base_url):
        msg = (f"Ollama at {base_url} is not reachable. "
               "It is a remote host, so it cannot be auto-started — "
               "check the URL and that Ollama is running there.")
        if fatal:
            print(f"ERROR: {msg}")
            sys.exit(1)
        raise RuntimeError(msg)
    print("Ollama not running — starting it now...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
            start_new_session=(sys.platform != "win32"),
        )
    except FileNotFoundError:
        msg = "'ollama' command not found. Install Ollama from https://ollama.com and re-run."
        if fatal:
            print(f"ERROR: {msg}")
            sys.exit(1)
        raise RuntimeError(msg)

    print("   Waiting for Ollama to be ready", end="", flush=True)
    for _ in range(30):
        time.sleep(1)
        print(".", end="", flush=True)
        if _ollama_is_running(base_url):
            print(" ready!")
            return
    msg = "Ollama did not start in time. Check your installation."
    print(f"\nERROR: {msg}")
    if fatal:
        sys.exit(1)
    raise RuntimeError(msg)


def _model_is_pulled(model: str, base_url: str = OLLAMA_BASE_URL) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=5) as resp:
            data = _json.loads(resp.read())
        local_names = [m["name"] for m in data.get("models", [])]
        # Exact match, or bare name (no tag) matches the `:latest` variant
        bare = model.split(":")[0]
        return model in local_names or (
            ":" not in model and f"{bare}:latest" in local_names
        )
    except Exception:
        return False


def _pull_model(model: str, fatal: bool = True):
    print(f"Model '{model}' not found locally — pulling now (this may take a few minutes)...")
    try:
        subprocess.run(["ollama", "pull", model], check=True)
    except subprocess.CalledProcessError:
        msg = f"Failed to pull model '{model}'. Try: ollama pull {model}"
        print(f"ERROR: {msg}")
        if fatal:
            sys.exit(1)
        raise RuntimeError(msg)
    print(f"Model '{model}' ready.")


def _ensure_ollama():
    """CLI-mode: auto-start Ollama and pull model; exits on failure."""
    if not _ollama_is_running():
        _start_ollama(fatal=True)
    else:
        print("Ollama already running")
    if not _model_is_pulled(OLLAMA_MODEL):
        _pull_model(OLLAMA_MODEL, fatal=True)
    else:
        print(f"Model '{OLLAMA_MODEL}' already available")


def _ensure_ollama_or_raise(model: str, base_url: str = OLLAMA_BASE_URL):
    """GUI-mode: auto-start Ollama and pull model; raises RuntimeError on failure."""
    if not _ollama_is_running(base_url):
        _start_ollama(fatal=False, base_url=base_url)
    if not _model_is_pulled(model, base_url):
        if not _is_localhost(base_url):
            raise RuntimeError(
                f"Model '{model}' is not available on the remote Ollama at {base_url}. "
                f"Pull it there first: ollama pull {model}"
            )
        _pull_model(model, fatal=False)


# ─────────────────────────────────────────────
#  CANDIDATE PROFILE (from profile.py, optional)
# ─────────────────────────────────────────────

try:
    from profile import CANDIDATE_PROFILE
except ImportError:
    CANDIDATE_PROFILE = ""


# ─────────────────────────────────────────────
#  LLM FACTORY
# ─────────────────────────────────────────────

def get_llm(llm_config: dict | None = None, json_mode: bool = False) -> LLM:
    """
    Build an LLM instance for any supported backend.

    llm_config keys:
        backend     : 'ollama' | 'deepseek' | 'anthropic' | 'openai' |
                      'gemini' | 'groq' | 'openrouter'
        model       : model name string (provider-specific)
        temperature : float (default 0.7)
        max_tokens  : int   (default 3000)
        api_key     : str   (overrides the corresponding env var)
        base_url    : str   (overrides default URL; required for openrouter)

    json_mode: for the ollama backend, ask the server to constrain output to
    valid JSON (used by the single-shot extraction calls). Ignored elsewhere —
    _robust_json_extract() remains the fallback for cloud backends.

    If llm_config is None, uses env-based auto-detect (CLI backwards compat):
        DEEPSEEK_API_KEY → DeepSeek
        else             → local Ollama default model
    """
    if llm_config is None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if api_key:
            print("Using DeepSeek API")
            return LLM(model="deepseek/deepseek-chat", api_key=api_key,
                       temperature=0.7, max_tokens=3000)
        print(f"No API key — using local Ollama ({OLLAMA_MODEL})")
        _ensure_ollama()
        _model_eff = _ensure_ctx_model(OLLAMA_MODEL)
        return LLM(model=f"ollama_chat/{_model_eff}", base_url=OLLAMA_BASE_URL,
                   temperature=0.7, max_tokens=3000,
                   extra_body=_ollama_extra_body(OLLAMA_MODEL))

    backend     = llm_config.get("backend", "ollama")
    model       = llm_config.get("model", OLLAMA_MODEL)
    temperature = float(llm_config.get("temperature", 0.7))
    max_tokens  = int(llm_config.get("max_tokens", 3000))
    api_key     = llm_config.get("api_key") or ""
    base_url    = llm_config.get("base_url") or ""

    def _require_key(env_var: str) -> str:
        key = api_key or os.getenv(env_var, "")
        if not key:
            raise ValueError(
                f"'{backend}' backend requires an API key. "
                f"Provide it in the sidebar or set {env_var} in your .env file."
            )
        return key

    if backend == "ollama":
        _base = base_url or OLLAMA_BASE_URL
        _ensure_ollama_or_raise(model, _base)
        # The real context window lives in a derived model (_ensure_ctx_model) —
        # Ollama's OpenAI-compatible endpoint ignores per-request options.
        # reasoning_effort (thinking off for qwen3 family) rides in extra_body,
        # which crewai's OpenAI-SDK client sends through as raw JSON body fields.
        _model_eff = _ensure_ctx_model(model, _base)
        _extra = {"response_format": {"type": "json_object"}} if json_mode else {}
        return LLM(model=f"ollama_chat/{_model_eff}", base_url=_base,
                   temperature=temperature, max_tokens=max_tokens,
                   extra_body=_ollama_extra_body(model), **_extra)

    elif backend == "deepseek":
        key = _require_key("DEEPSEEK_API_KEY")
        return LLM(model=f"deepseek/{model}", api_key=key,
                   temperature=temperature, max_tokens=max_tokens,
                   timeout=180)

    elif backend == "anthropic":
        key = _require_key("ANTHROPIC_API_KEY")
        return LLM(model=f"anthropic/{model}", api_key=key,
                   temperature=temperature, max_tokens=max_tokens,
                   timeout=180)

    elif backend == "openai":
        key = _require_key("OPENAI_API_KEY")
        return LLM(model=model, api_key=key,
                   temperature=temperature, max_tokens=max_tokens,
                   timeout=180)

    elif backend == "gemini":
        key = _require_key("GOOGLE_API_KEY")
        # Use Google's OpenAI-compatible endpoint instead of the native google-genai
        # provider — the native provider is locked to v1beta and has very limited
        # model availability. The OpenAI-compatible endpoint supports the full range.
        return LLM(model=f"openai/{model}", api_key=key,
                   base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                   temperature=temperature, max_tokens=max_tokens,
                   timeout=180)

    elif backend == "groq":
        key = _require_key("GROQ_API_KEY")
        return LLM(model=f"groq/{model}", api_key=key,
                   temperature=temperature, max_tokens=max_tokens,
                   timeout=180)

    elif backend == "openrouter":
        key = _require_key("OPENROUTER_API_KEY")
        _base = base_url or "https://openrouter.ai/api/v1"
        return LLM(model=f"openrouter/{model}", api_key=key, base_url=_base,
                   temperature=temperature, max_tokens=max_tokens,
                   timeout=180)

    else:
        raise ValueError(
            f"Unknown backend '{backend}'. "
            "Supported: ollama, deepseek, anthropic, openai, gemini, groq, openrouter"
        )


# ─────────────────────────────────────────────
#  COLLECT JOB INFO (CLI only)
# ─────────────────────────────────────────────

def collect_raw_paste() -> str:
    print("\n" + "="*60)
    print("  COVER LETTER CREW")
    print("="*60)
    print("\nPaste the FULL job page (copy everything from LinkedIn/job board).")
    print("Press Enter twice when done.\n")
    lines = []
    while True:
        line = input()
        if line == "" and lines and lines[-1] == "":
            break
        lines.append(line)
    return "\n".join(lines).strip()


# ─────────────────────────────────────────────
#  JOB PAGE PARSER
# ─────────────────────────────────────────────

def _robust_json_extract(text: str) -> dict | None:
    """
    Multi-strategy JSON extractor for unreliable LLM output.
    Tries progressively looser approaches before giving up.
    """
    import re

    def _try_parse(s: str) -> dict | None:
        try:
            obj = _json.loads(s.strip())
            return obj if isinstance(obj, dict) else None
        except _json.JSONDecodeError:
            return None

    # Strategy 1: direct parse
    if r := _try_parse(text):
        return r

    # Strategy 2: strip all markdown fence variants (```json, ```JSON, ```)
    stripped = re.sub(r'```[a-zA-Z]*\s*', '', text).replace('```', '').strip()
    if r := _try_parse(stripped):
        return r

    # Strategy 3: find outermost { ... } block accounting for nesting
    depth, start = 0, -1
    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start != -1:
                if r := _try_parse(text[start:i + 1]):
                    return r

    # Strategy 4: all {...} blobs, largest first
    for blob in sorted(re.findall(r'\{.*?\}', text, re.DOTALL), key=len, reverse=True):
        if r := _try_parse(blob):
            return r

    return None


def clean_job_paste(raw: str, llm: LLM, interactive: bool = True,
                    no_think: str = "") -> dict:
    """
    Use an LLM agent to strip UI noise and extract structured job data.
    Set interactive=False for Streamlit (skips input() confirmation).
    no_think: pass _no_think_suffix(llm_config) to disable qwen3 thinking mode.
    """
    cleaner = Agent(
        role="Job Page Parser",
        goal="Extract structured job information from raw job posting text.",
        backstory="Expert at parsing LinkedIn and job board pages. Ignores UI chrome, extracts only job content.",
        llm=llm,
        verbose=False
    )

    parse_task = Task(
        description=f"""
The text below is a raw copy-paste from a job posting page.
Ignore all UI elements (buttons, nav, share/save prompts, premium upsells, network widgets).

Extract ONLY these 5 fields and return a JSON object with exactly these keys:
- company_name   : the actual company name. IMPORTANT: if the posting does not name the
                   company (e.g. says "our client", "unser Kunde", "a leading company"),
                   set this to "your organisation" — do NOT invent or guess a name.
- job_title      : the exact job title
- location       : city and country only (e.g. "Friedrichshafen, Germany")
- ref_number     : job reference number if stated, otherwise empty string
- job_description: ONLY the real job description text (responsibilities, qualifications, requirements). Keep bullet points.

Return ONLY the JSON object. No markdown fences, no explanation.

RAW TEXT:
{raw}
{no_think}""",
        agent=cleaner,
        expected_output='JSON object with keys: company_name, job_title, location, ref_number, job_description'
    )

    mini_crew = Crew(agents=[cleaner], tasks=[parse_task], verbose=False)
    result    = mini_crew.kickoff()
    raw_out   = _strip_think(str(result).strip())

    data = _robust_json_extract(raw_out)
    if data is None:
        print("Parser returned non-JSON — using raw text as job description.")
        data = {
            "company_name":    "",
            "job_title":       "",
            "location":        "",
            "ref_number":      "",
            "job_description": raw_out
        }

    if interactive:
        print("\nExtracted job info:")
        print(f"   Company  : {data.get('company_name','')}")
        print(f"   Title    : {data.get('job_title','')}")
        print(f"   Location : {data.get('location','')}")
        print(f"   Ref      : {data.get('ref_number','—')}")
        confirm = input("\nLooks right? (Enter to continue, or type correction): ").strip()
        if confirm:
            print("(You can edit company_name, job_title, location, ref_number)")
            for key in ("company_name", "job_title", "location", "ref_number"):
                val = input(f"  {key} [{data.get(key,'')}]: ").strip()
                if val:
                    data[key] = val

    return {
        "company":    data.get("company_name", "Unknown"),
        "job_title":  data.get("job_title",    "Unknown"),
        "location":   data.get("location",     ""),
        "job_desc":   data.get("job_description", raw),
        "ref_number": data.get("ref_number",   "")
    }


# ─────────────────────────────────────────────
#  REVIEWER OUTPUT PARSER
# ─────────────────────────────────────────────

def _parse_reviewer_output(text: str) -> dict:
    """
    Extract 2 final letters from the combined reviewer output.
    Looks for labels EN_FORMAL_FINAL:, EN_MODERN_FINAL:.
    Returns a dict with those keys; empty string if a section wasn't found.
    """
    import re
    text    = _strip_think(text)
    keys    = ["en_formal", "en_modern"]
    labels  = ["EN_FORMAL_FINAL", "EN_MODERN_FINAL"]
    pattern = "|".join(re.escape(l) for l in labels)
    parts   = re.split(rf'({pattern})\s*:', text, flags=re.IGNORECASE)

    result = {k: "" for k in keys}
    i = 1  # parts[0] is text before the first label
    while i < len(parts) - 1:
        raw_label = parts[i].strip().upper()
        body      = parts[i + 1].strip() if (i + 1) < len(parts) else ""
        for key, label in zip(keys, labels):
            if raw_label == label:
                result[key] = body
                break
        i += 2
    return result


# ─────────────────────────────────────────────
#  BUILD THE CREW  (8-task pipeline, 7 agents)
# ─────────────────────────────────────────────
#
#  Task index map (used in generate_cover_letters):
#    0  jd_task          — Job Analyst
#    1  resume_task      — Resume Analyzer
#    2  gap_task         — Skill Gap Mapper + Truth Anchor
#    3  ats_task         — ATS Keyword Optimizer
#    4  en_formal_task   — EN Formal Writer
#    5  en_modern_task   — EN Modern Writer
#    6  fact_check_task  — Fact Checker (violation report)
#    7  review_task      — Tone + Grammar Reviewer  ← final output

# ─────────────────────────────────────────────
#  PROFILE PARSING
# ─────────────────────────────────────────────

_PARSE_PROFILE_PROMPT = """\
Extract structured information from this CV. Return ONLY valid JSON — no commentary, no markdown fences.

JSON format:
{
  "name": "Full Name or null",
  "current_role": "Most recent job title or null",
  "total_years": "e.g. 4 or null",
  "companies": [
    {"name": "Company", "role": "Job title", "start": "YYYY or null", "end": "YYYY or Present", "achievements": ["specific achievement 1", "specific achievement 2"]}
  ],
  "skills_technical": ["skill1", "skill2"],
  "skills_tools": ["tool1", "tool2"],
  "certifications": ["cert1"],
  "education": [{"degree": "...", "institution": "...", "year": "..."}],
  "languages_spoken": ["English", "German"]
}

CV text:
"""


def parse_profile(profile_text: str, llm: LLM, no_think: str = "") -> dict:
    """One LLM call → structured profile dict. Fallback: {"raw": profile_text}."""
    try:
        raw = llm.call([{"role": "user",
                         "content": _PARSE_PROFILE_PROMPT + profile_text[:8000] + "\n" + no_think}])
        if raw is None:
            return {"raw": profile_text}
        raw = _strip_think(raw if isinstance(raw, str) else str(raw))
        m = re.search(r'\{[\s\S]+\}', raw)
        if m:
            return _json.loads(m.group(0))
    except Exception as e:
        if os.environ.get("DEBUG"):
            print(f"[DEBUG] parse_profile failed: {e}")
    return {"raw": profile_text}


def _format_structured_profile(p: dict) -> str:
    """Convert parsed profile dict → clean labelled string for agent consumption."""
    if "raw" in p:
        return p["raw"]
    lines = []
    if p.get("name"):
        lines.append(f"CANDIDATE: {p['name']}")
    if p.get("current_role"):
        lines.append(f"CURRENT ROLE: {p['current_role']}")
    if p.get("total_years"):
        lines.append(f"EXPERIENCE: {p['total_years']} years")
    companies = p.get("companies") or []
    if companies:
        lines.append("COMPANIES:")
        for c in companies:
            end = c.get("end") or "Present"
            lines.append(f"  - {c.get('name','')} ({c.get('start','?')}–{end}) as {c.get('role','')}")
            for ach in (c.get("achievements") or [])[:3]:
                lines.append(f"      · {ach}")
    if p.get("skills_technical"):
        lines.append(f"TECHNICAL SKILLS: {', '.join(p['skills_technical'])}")
    if p.get("skills_tools"):
        lines.append(f"TOOLS: {', '.join(p['skills_tools'])}")
    if p.get("certifications"):
        lines.append(f"CERTIFICATIONS: {', '.join(p['certifications'])}")
    if p.get("education"):
        edu = p["education"]
        lines.append("EDUCATION:")
        for e in edu:
            lines.append(f"  - {e.get('degree','')} — {e.get('institution','')} ({e.get('year','')})")
    if p.get("languages_spoken"):
        lines.append(f"LANGUAGES: {', '.join(p['languages_spoken'])}")
    return "\n".join(lines)


_INDUSTRY_CONTEXT = {
    "Aerospace & Defence":   "Emphasise precision engineering, certification standards (DO-178C, DO-254), safety-critical development, and regulatory compliance. Aerospace employers value zero-defect culture.",
    "Automotive & Embedded": "Emphasise AUTOSAR, A-SPICE, functional safety (ISO 26262), real-time systems, and ECU development. Automotive employers value rigorous process and tool-chain mastery.",
    "Software Engineering":  "Emphasise architecture decisions, scalability, code quality, and measurable impact (performance gains, uptime improvements). Tech employers value outcomes over process.",
    "Finance & Banking":     "Emphasise accuracy, risk awareness, regulatory knowledge, and delivery under pressure. Finance employers value reliability and numerical precision.",
    "Healthcare & MedTech":  "Emphasise IEC 62304, patient safety, FDA/CE regulatory context, and validation rigor. MedTech employers value compliance and documentation discipline.",
    "Generic":               "",
}


# Whole-word / phrase signatures used to auto-detect the industry from a JD.
# Kept discriminative on purpose — standards, tools and domain nouns rather than
# generic words — so a single strong hit is meaningful.
_INDUSTRY_KEYWORDS = {
    "Aerospace & Defence": [
        "aerospace", "avionics", "aircraft", "aviation", "airborne", "flight control",
        "defence", "defense", "do-178", "do-254", "arp4754", "arp 4754",
        "satellite", "spacecraft", "radar", "missile", "uav", "fighter",
    ],
    "Automotive & Embedded": [
        "automotive", "autosar", "iso 26262", "iso26262", "aspice", "a-spice",
        "ecu", "adas", "in-vehicle", "powertrain", "misra", "functional safety",
        "asil", "motor control", "battery management", "vehicle dynamics",
    ],
    "Software Engineering": [
        "software engineer", "backend", "back-end", "frontend", "front-end",
        "full-stack", "fullstack", "microservices", "kubernetes", "docker",
        "devops", "aws", "azure", "react", "node.js", "saas",
        "scalability", "web application", "rest api", "distributed systems",
    ],
    "Finance & Banking": [
        "finance", "banking", "fintech", "trading", "investment", "hedge fund",
        "payments", "insurance", "financial services", "quantitative", "quant",
        "risk management", "capital markets", "asset management", "brokerage",
    ],
    "Healthcare & MedTech": [
        "healthcare", "medtech", "medical device", "iec 62304", "iso 13485",
        "clinical", "patient", "diagnostic", "pharmaceutical", "biomedical",
        "fda", "ce marking", "in-vitro", "hospital",
    ],
}

# Values that mean "please pick the industry for me" rather than a fixed sector.
_AUTO_INDUSTRY_SENTINELS = {"", "auto", "auto-detect", "auto-detect (from jd)"}


def infer_industry(job: dict) -> str:
    """Guess the target industry from a parsed job dict via keyword scoring.

    The job title is weighted 3x (it is the strongest single signal), then the
    description/company/location body. Returns 'Generic' when nothing scores,
    so the writers fall back to a neutral tone rather than a wrong sector.
    """
    title = (job.get("job_title") or "").lower()
    body  = " ".join(str(job.get(k) or "") for k in ("job_desc", "company", "location")).lower()

    scores: dict[str, int] = {}
    for industry, terms in _INDUSTRY_KEYWORDS.items():
        score = 0
        for term in terms:
            pat = r"\b" + re.escape(term) + r"\b"
            score += 3 * len(re.findall(pat, title))
            score += len(re.findall(pat, body))
        scores[industry] = score

    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "Generic"


def _resolve_industry(industry: str | None, job: dict) -> str:
    """Turn an 'Auto-detect' request (or blank) into a concrete sector."""
    if industry is None or str(industry).strip().lower() in _AUTO_INDUSTRY_SENTINELS:
        return infer_industry(job)
    return industry


_TRUTH_RULES_FULL = """\
RULE 1 — EQUIVALENCE:
Adjacent experience is NOT the same as direct expertise. Having background in field A
does not make someone an expert in field B, even if A and B are related.
Common false equivalences to watch for (examples — not exhaustive):
  × control systems ≠ propulsion engineering
  × embedded software ≠ systems engineering
  × simulation/modelling ≠ CFD or FEA
  × motor/drive control ≠ engine or turbine design
  × avionics / DO-178C ≠ flight mechanics or aerodynamics
  × automotive embedded ≠ aerospace structures
  × manned aircraft / fighter jets ≠ unmanned aerial systems (UAS) or drone development
  × aerospace mechanical / systems engineering ≠ aerospace software or avionics development
  × military aircraft geometry / hydraulics ≠ UAS software or flight control software
  × backend software ≠ frontend or DevOps
  × data analysis ≠ machine learning engineering
  × machine learning ≠ data engineering or MLOps
  × academic research ≠ industry production experience
  × mechanical design ≠ manufacturing engineering
  × signal processing ≠ RF/antenna design
  × project management ≠ product management
  × finance analysis ≠ trading/quant development
  × general programming ≠ real-time or safety-critical programming
  These are ADJACENT — transferable foundation exists — but NOT interchangeable.

RULE 2 — JOB TITLE CLAIMS:
Only assign a job title (e.g. "propulsion engineer", "ML engineer", "DevOps engineer",
"data scientist") if that exact title or a very close synonym appears in the candidate profile.
Holding a neighbouring role does NOT qualify someone for a different title.

RULE 3 — TOOL CLAIMS:
Only mention a tool, software, or language if it is explicitly named in the candidate profile.
If it appears in the JD but NOT in the profile → add it to PROHIBITED_CLAIMS.

RULE 4 — DEPTH CALIBRATION:
Choose the right claim strength based on evidence:
  "I am a specialist in X"    → X is the candidate's primary domain with multiple deep examples
  "I have expertise in X"     → X appears with specific, measurable outcomes
  "I have experience with X"  → X is mentioned with at least one concrete project
  "I have exposure to X"      → X is mentioned once, briefly
  "X is an area I am developing" → X appears in the JD but not the profile at all
  Never use stronger language than the evidence supports.

RULE 5 — SAFE FRAMING TEMPLATES (use these for partial/weak/missing items):
  Strong adjacent: "[Profile skill] experience provides a strong foundation for [JD skill] integration."
  Moderate adjacent: "My work in [profile domain] involved [specific transferable aspect] relevant to [JD domain]."
  Weak/missing: "While [JD skill] is an area I am building toward, my [closest profile skill] gives me [specific transferable element]."
  Domain bridge: "[Profile domain] and [JD domain] share [specific common element] — my background in the former applies directly."
"""

# Compact variant for small local models (≤8B): same rules, fewer examples, less
# prose — long prompts measurably degrade instruction-following on small models.
_TRUTH_RULES_COMPACT = """\
RULE 1 — EQUIVALENCE: adjacent experience is NOT direct expertise. Examples:
  × control systems ≠ propulsion · embedded software ≠ systems engineering
  × motor/drive control ≠ engine design · automotive embedded ≠ aerospace structures
  × simulation/modelling ≠ CFD/FEA · general programming ≠ safety-critical programming
  × manned aircraft ≠ UAS/drones · aerospace systems eng ≠ avionics/flight software
  Adjacent = transferable foundation, NOT interchangeable.

RULE 2 — JOB TITLES: only use a job title if it (or a close synonym) appears in the profile.

RULE 3 — TOOLS: only mention a tool/language explicitly named in the profile.
If the JD requires it but the profile lacks it → put it in PROHIBITED_CLAIMS.

RULE 4 — CLAIM STRENGTH: "expertise" needs measurable outcomes; "experience" needs one
concrete project; "exposure" for single mentions. Never overstate.

RULE 5 — SAFE FRAMING for partial/missing items, e.g.:
  "My work in [profile domain] involved [transferable aspect] relevant to [JD domain]."
  "While [JD skill] is an area I am building toward, my [profile skill] gives me [transferable element]."
"""


def build_crew(job: dict, llm: LLM, profile_text: str | None = None, step_callback=None,
               candidate_name: str = "the candidate",
               structured_profile: dict | None = None,
               industry: str = "Generic",
               analysis_llm: LLM | None = None,
               small_model: bool = False,
               no_think: str = "") -> Crew:
    """
    Build and return a 7-agent, 8-task CrewAI Crew (English-only pipeline).
    profile_text  : raw CV text (fallback if structured_profile not supplied)
    structured_profile : pre-parsed profile dict from parse_profile()
    candidate_name: used in sign-offs and writer context headers
    industry      : sector hint injected into each writer task
    analysis_llm  : low-temperature LLM for the structured analysis tasks
                    (JD/resume/gap/ATS/fact-check); defaults to `llm`
    small_model   : use compact prompt variants (small local models)
    no_think      : suffix appended to every task ("/no_think" for qwen3 via Ollama)
    """
    _profile = profile_text if profile_text is not None else CANDIDATE_PROFILE
    _profile_block = _format_structured_profile(structured_profile) if structured_profile is not None else _profile
    _ref     = job.get('ref_number') or 'N/A'
    industry = _resolve_industry(industry, job)
    _industry_ctx = _INDUSTRY_CONTEXT.get(industry, "")
    analysis_llm = analysis_llm or llm
    _truth_rules = _TRUTH_RULES_COMPACT if small_model else _TRUTH_RULES_FULL

    # ══════════════════════════════════════════
    #  AGENTS
    # ══════════════════════════════════════════

    job_analyst = Agent(
        role="Job Analyst",
        goal="Extract the key technical requirements, must-haves, and culture signals from a job description.",
        backstory="Expert technical recruiter with 15 years in embedded systems and aerospace hiring. Reads JDs instantly and spots what really matters.",
        llm=analysis_llm, verbose=False
    )

    resume_analyzer = Agent(
        role="Resume Analyzer",
        goal="Deeply analyse the candidate's CV and produce a structured skills and experience breakdown.",
        backstory="Senior career consultant specialising in STEM profiles. Extracts concrete skills, domain expertise, and standout achievements from CVs.",
        llm=analysis_llm, verbose=False
    )

    skill_gap_mapper = Agent(
        role="Skill Gap Mapper",
        goal="Compare the job requirements against the candidate's profile and identify strong matches, partial matches, and gaps.",
        backstory="Talent analyst who maps candidate profiles to job requirements with precision. Knows how to frame gaps honestly without hurting the application.",
        llm=analysis_llm, verbose=False
    )

    ats_optimizer = Agent(
        role="ATS Keyword Optimizer",
        goal="Identify the exact ATS keywords from the job description and create keyword-enriched talking points the writers must use.",
        backstory="ATS systems expert who knows that 75% of CVs are rejected by algorithms before a human reads them. Ensures the right keywords appear naturally in the letter.",
        llm=analysis_llm, verbose=False
    )

    en_formal_writer = Agent(
        role="Formal English Cover Letter Writer",
        goal="Write a formal, professional English cover letter using the ATS-optimized analysis.",
        backstory="Writes polished formal business letters for aerospace and defence companies. Structured paragraphs, confident tone, traditional style. Max 4 paragraphs.",
        llm=llm, verbose=False
    )

    en_modern_writer = Agent(
        role="Modern English Cover Letter Writer",
        goal="Write a direct, confident, modern English cover letter using the ATS-optimized analysis.",
        backstory="Writes punchy, direct cover letters that open with the strongest hook. No filler phrases, no corporate speak. Max 4 paragraphs.",
        llm=llm, verbose=False
    )

    fact_checker = Agent(
        role="Cover Letter Fact Checker",
        goal="Find every claim in the cover letters that cannot be verified from the candidate profile. Produce a structured violation report.",
        backstory="Rigorous fact-checking editor for engineering cover letters. Sole job is finding unverifiable claims — does NOT evaluate writing quality or style.",
        llm=analysis_llm, verbose=False
    )

    tone_grammar_reviewer = Agent(
        role="Tone and Grammar Reviewer",
        goal="Apply fact-check corrections and review both English cover letter drafts for robotic AI language, grammar errors, and incorrect technical terms. Output corrected versions.",
        backstory="Senior editor who has reviewed thousands of engineering job applications. Catches AI-sounding phrases instantly and rewrites them to sound natural and human.",
        llm=llm, verbose=False
    )

    # ══════════════════════════════════════════
    #  TASKS
    # ══════════════════════════════════════════

    # Task 0 — Job Analysis
    jd_task = Task(
        description=f"""
Analyse this job description. Be concise.

POSITION: {job.get('job_title', '')} at {job.get('company', '')}
LOCATION: {job.get('location', '')}

JOB DESCRIPTION:
{job.get('job_desc', '')}

Output EXACTLY:

TECHNICAL_REQUIREMENTS:
1. [requirement]
2. [requirement]
3. [requirement]
4. [requirement]
5. [requirement]

SOFT_SKILLS:
1. [skill]
2. [skill]
3. [skill]

KEYWORDS: [comma-separated — exact terms from the JD]

TOOLS_AND_STANDARDS: [comma-separated]

COMPANY_CULTURE: [1 sentence on company culture/values from the JD]
{no_think}""",
        agent=job_analyst,
        expected_output="Structured JD analysis: TECHNICAL_REQUIREMENTS, SOFT_SKILLS, KEYWORDS, TOOLS_AND_STANDARDS, COMPANY_CULTURE"
    )

    # Task 1 — Resume Analysis
    resume_task = Task(
        description=f"""
Analyse this candidate's CV. Be specific — extract concrete facts, not vague summaries.

CANDIDATE PROFILE:
{_profile_block}

Output EXACTLY:

CORE_TECHNICAL_SKILLS:
[comma-separated list of specific skills]

DOMAIN_EXPERTISE:
[comma-separated domains, e.g. AUTOSAR, Motor Control, Aerospace Systems]

EXPERIENCE_HIGHLIGHTS:
1. [Role at Company — key technical achievement in one sentence]
2. [Role at Company — key technical achievement]
3. [Role at Company — key technical achievement]

STANDARDS_AND_CERTS:
[comma-separated: ISO 26262, A-SPICE, MISRA C, DO-178C, etc.]

UNIQUE_BACKGROUND:
[1 sentence on what makes this candidate rare or unusual]

LANGUAGE_SKILLS:
[languages and proficiency level]
{no_think}""",
        agent=resume_analyzer,
        expected_output="Structured CV analysis: CORE_TECHNICAL_SKILLS, DOMAIN_EXPERTISE, EXPERIENCE_HIGHLIGHTS, STANDARDS_AND_CERTS, UNIQUE_BACKGROUND, LANGUAGE_SKILLS"
    )

    # Task 2 — Skill Gap Mapping + Truth Anchor
    gap_task = Task(
        description=f"""
Using the JD Analysis (Task 0) and the Resume Analysis (Task 1), map the candidate to this role.

══════════════════════════════════════════════════════
UNIVERSAL DOMAIN TRUTH RULES — apply these before writing any output section.
These rules apply to ALL profiles and ALL industries.
══════════════════════════════════════════════════════

{_truth_rules}
══════════════════════════════════════════════════════
Now apply these rules and output EXACTLY the following sections:
══════════════════════════════════════════════════════

STRONG_MATCHES:
1. [JD requirement] → [candidate evidence — quote specific role, tool, standard, or achievement from profile]
2. [JD requirement] → [candidate evidence]
3. [JD requirement] → [candidate evidence]
4. [JD requirement] → [candidate evidence]

PARTIAL_MATCHES:
1. [JD requirement] — [candidate has adjacent but not direct experience] → [exact safe framing sentence to use]
2. [JD requirement] — [framing strategy] → [safe framing sentence]

PROHIBITED_CLAIMS:
[List every skill, tool, role title, or domain the JD requires that has NO direct evidence in the candidate
profile. These must NOT appear as claims in the cover letter — not even with hedging like "exposure to"
or "some experience with". One item per line.]

SAFE_FRAMING:
[For each item in PROHIBITED_CLAIMS and each PARTIAL_MATCH, write ONE specific transferable bridge sentence.
Apply RULE 5 templates above. Format: item → sentence]

OPENING_HOOK: [one powerful specific first sentence — concrete achievement from profile, NOT "With extensive experience in..."]

UNIQUE_ANGLE: [1–2 sentences: what makes this candidate valuable for THIS company — based only on profile facts]

AEROSPACE_FLAG: [YES if the job description mentions any of: avionik, avionics, DO-178C, aerospace,
luft, raumfahrt, flight, defence, rüstung, aircraft — otherwise NO]

If AEROSPACE_FLAG is YES, also output:
DOMAIN_HIGHLIGHT: [The candidate's most relevant prior experience in aerospace, aviation, defence, or
any safety-critical regulated domain — from the profile only. What they did, which standards or tools
were involved, and how it connects to THIS job. Do not invent.
IMPORTANT DISTINCTIONS — apply RULE 1 here:
  - If the job targets UAS/drones/unmanned and the candidate's aerospace role was with MANNED aircraft
    (fighter jets, combat aircraft, commercial aircraft, helicopters), state the gap explicitly.
    Manned aircraft ≠ UAS. Put "UAS direct experience" in PROHIBITED_CLAIMS and provide SAFE_FRAMING
    for the bridge instead (e.g. systems engineering principles, MATLAB/Simulink dynamic modelling).
  - If the candidate's aerospace role was mechanical or systems engineering (geometry, hydraulics, ECS,
    PLM tools) rather than software development, do NOT present it as aerospace software experience.
    The candidate's software career begins at their first software-titled role — state this clearly.]
{no_think}""",
        agent=skill_gap_mapper,
        expected_output="Truth-anchored skill map: STRONG_MATCHES, PARTIAL_MATCHES, PROHIBITED_CLAIMS, SAFE_FRAMING, OPENING_HOOK, UNIQUE_ANGLE, AEROSPACE_FLAG, DOMAIN_HIGHLIGHT (if aerospace)",
        context=[jd_task, resume_task]
    )

    # Task 3 — ATS Keyword Optimization
    ats_task = Task(
        description=f"""
Using the JD Analysis (Task 0) and Skill Gap Map (Task 2), create ATS-optimised talking points.

ATS systems scan for exact keyword matches before a human reads the letter. Your job is to:
1. List the exact keywords from the JD that MUST appear in the cover letter
2. Map each keyword to a specific candidate experience phrase that embeds it naturally

Output EXACTLY:

MUST_USE_KEYWORDS_EN: [keywords in ENGLISH — translate any German JD terms:
sicherheitskritisch→safety-critical, hardwarenahe→low-level, echtzeitfähig→real-time-capable,
Embedded Entwicklung→embedded development, zeitkritisch→time-critical]
MUST_USE_KEYWORDS_DE: [same keywords in GERMAN — keep as original JD phrasing]

KEYWORD_TALKING_POINTS:
1. [Keyword EN / Keyword DE] → "English sentence: [embed English keyword naturally] | German sentence: [embed German keyword naturally]"
2. [Keyword EN / Keyword DE] → "English sentence: [...] | German sentence: [...]"
3. [Keyword EN / Keyword DE] → "English sentence: [...] | German sentence: [...]"
4. [Keyword EN / Keyword DE] → "English sentence: [...] | German sentence: [...]"
5. [Keyword EN / Keyword DE] → "English sentence: [...] | German sentence: [...]"

ATS_OPENING_EN: [opening hook in English — embeds top 2 English keywords]
ATS_OPENING_DE: [opening hook in German — embeds top 2 German keywords]
{no_think}""",
        agent=ats_optimizer,
        expected_output="ATS optimization: MUST_USE_KEYWORDS, KEYWORD_TALKING_POINTS, ATS_OPENING",
        context=[jd_task, gap_task]
    )

    # Shared writer rules — full for cloud models, compact for small local models
    # (long rule lists measurably degrade instruction-following on ≤8B models).
    _exempt_nouns = ("DO-178C, AUTOSAR, VxWorks, ASIL B, MISRA C, MIL/SIL/HIL, CANoe, "
                     "WinIdea, Simulink, DOORS, FOC, IPMSM, MXAM, Polyspace, TESSY, A-SPICE")
    if small_model:
        _formal_rules = f"""\
STRICT RULES:
- ENGLISH ONLY: translate any German JD term using MUST_USE_KEYWORDS_EN. Keep these
  technical proper nouns exactly as-is: {_exempt_nouns}.
- STYLE: formal flowing prose with connector words (Moreover, Furthermore). No em-dashes.
- OPENER: do NOT start with "With extensive experience in..." — open with a specific achievement.
- TRUTH: never claim any skill, tool, or job title listed in PROHIBITED_CLAIMS (Task 2) —
  use the exact SAFE_FRAMING sentence instead. Never invent years of experience.
- If AEROSPACE_FLAG is YES, Para 2 MUST use DOMAIN_HIGHLIGHT from Task 2.
- Do NOT address the recruiter agency as the employer — use the company name from the header.
Output ONLY the letter body. No subject line, no date, no commentary, no markdown."""
        _modern_rules = f"""\
STRICT RULES:
- ENGLISH ONLY: translate any German JD term using MUST_USE_KEYWORDS_EN. Keep these
  technical proper nouns exactly as-is: {_exempt_nouns}.
- STYLE: short declarative sentences. Em-dashes (—) for emphasis. No connector words
  like "Furthermore", "Moreover", "Additionally".
- OPENER: do NOT start with "With extensive experience in..." — open with a punchy specific fact.
- TRUTH: never claim any skill, tool, or job title listed in PROHIBITED_CLAIMS (Task 2) —
  use the exact SAFE_FRAMING sentence instead. Never invent years of experience.
- If AEROSPACE_FLAG is YES, Para 2 MUST use DOMAIN_HIGHLIGHT from Task 2.
- Do NOT address the recruiter agency as the employer — use the company name from the header.
Output ONLY the letter body. No subject line, no date, no commentary, no markdown."""
    else:
        _formal_rules = f"""\
STRICT RULES:
- LANGUAGE: Do NOT write any German word in this letter. If a term from the JD is in German,
  translate it using MUST_USE_KEYWORDS_EN — never embed the German form.
  These technical proper nouns are exempt (keep exactly as-is): {_exempt_nouns}.
  All other words must be English.
- STYLE: Formal flowing prose. Use connector words (Moreover, Furthermore, Additionally).
  Sentences may be long with subordinate clauses. No em-dashes for emphasis.
- OPENER: Do NOT start Para 1 with "With extensive experience in..." — open with a specific
  achievement or direct value statement (e.g. "DO-178C compliance has been central to my work...")
- TRUTH CONSTRAINT: Do NOT claim expertise in any skill listed under PROHIBITED_CLAIMS from Task 2.
  If the job requires it, use ONLY the exact sentence from SAFE_FRAMING from Task 2 — nothing stronger.
  Do NOT call the candidate a job title (e.g. "propulsion engineer", "ML engineer") unless that exact
  title appears in their profile. Do NOT mention any tool or standard not in the candidate profile.
- Do NOT invent years of experience — only use durations explicitly stated in the profile above.
- If AEROSPACE_FLAG is YES, Para 2 MUST use DOMAIN_HIGHLIGHT from Task 2.
- Do NOT address the recruiter agency as the employer — use the company name from the position header.
Output ONLY the letter body. No subject line, no date, no commentary, no markdown."""
        _modern_rules = f"""\
STRICT RULES:
- LANGUAGE: Do NOT write any German word in this letter. If a term from the JD is in German,
  translate it using MUST_USE_KEYWORDS_EN — never embed the German form.
  These technical proper nouns are exempt (keep exactly as-is): {_exempt_nouns}.
  All other words must be English.
- STYLE: Short declarative sentences only. Use em-dashes (—) for emphasis. Zero connector words
  like "Furthermore", "Moreover", "Additionally" — each sentence must stand alone.
- OPENER: Do NOT start Para 1 with "With extensive experience in..." — open with a punchy
  specific fact drawn from the candidate profile (e.g. "Safety-critical embedded software in C —
  that is my domain." or state a specific role/achievement that directly matches the JD)
- TRUTH CONSTRAINT: Do NOT claim expertise in any skill listed under PROHIBITED_CLAIMS from Task 2.
  If the job requires it, use ONLY the exact sentence from SAFE_FRAMING from Task 2 — nothing stronger.
  Do NOT call the candidate a job title (e.g. "propulsion engineer", "ML engineer") unless that exact
  title appears in their profile. Do NOT mention any tool or standard not in the candidate profile.
- Do NOT invent years of experience — only use durations explicitly stated in the profile above.
- If AEROSPACE_FLAG is YES, Para 2 MUST use DOMAIN_HIGHLIGHT from Task 2.
- Do NOT address the recruiter agency as the employer — use the company name from the position header.
Output ONLY the letter body. No subject line, no date, no commentary, no markdown."""

    # Task 4 — EN Formal
    en_formal_task = Task(
        description=f"""
Write a FORMAL English cover letter for the position below.
You have access to: Job Analysis (Task 0), Skill Gap Map (Task 2), ATS Talking Points (Task 3).

Candidate: {candidate_name}
Position: {job['job_title']}
Company: {job['company']}
Location: {job['location']}
Reference: {_ref}

CANDIDATE PROFILE (use specific facts from here — job titles, companies, achievements):
{_profile_block}
{"" if not _industry_ctx else f"INDUSTRY CONTEXT: {_industry_ctx}"}

MANDATORY STRUCTURE — write EXACTLY 4 paragraphs, no bullet points:

PARAGRAPH 1 — Opening (3 sentences minimum):
First line: "Dear Hiring Manager," (alone on its own line)
Second sentence MUST be exactly: "I am writing to apply for the {job['job_title']} position at {job['company']}."
Then use the ATS_OPENING or OPENING_HOOK from context as the next sentence.
Establish the single strongest qualification that makes you right for this specific role.

PARAGRAPH 2 — Specific Evidence (4 sentences minimum):
Reference 2–3 STRONG_MATCHES from the skill gap analysis by name — use exact job titles,
company names, and what you specifically built or achieved.
Embed KEYWORD_TALKING_POINTS naturally. Be concrete — include standards, tools, systems.

PARAGRAPH 3 — Company/Role Fit (3 sentences minimum):
Use the UNIQUE_ANGLE from the gap analysis.
Reference one specific requirement from the job description (e.g. DO-178C, VxWorks, RTOS).
Explain why this role is a logical next step from your background.

PARAGRAPH 4 — Closing (2–3 sentences):
Express clear interest in discussing further. State you look forward to an interview.
Sign off: "Mit freundlichen Grüßen / Kind regards,\\n{candidate_name}"

{_formal_rules}
{no_think}""",
        agent=en_formal_writer,
        expected_output="Formal English cover letter body, exactly 4 paragraphs, plain text",
        context=[jd_task, gap_task, ats_task]
    )

    # Task 5 — EN Modern
    en_modern_task = Task(
        description=f"""
Write a MODERN, DIRECT English cover letter for the position below.
You have access to: Job Analysis (Task 0), Skill Gap Map (Task 2), ATS Talking Points (Task 3).

Candidate: {candidate_name}
Position: {job['job_title']}
Company: {job['company']}
Location: {job['location']}
Reference: {_ref}

CANDIDATE PROFILE (use specific facts from here — job titles, companies, achievements):
{_profile_block}
{"" if not _industry_ctx else f"INDUSTRY CONTEXT: {_industry_ctx}"}

MANDATORY STRUCTURE — write EXACTLY 4 paragraphs, no bullet points, punchy direct tone:

PARAGRAPH 1 — Hook (3 sentences minimum):
First line: "Dear Hiring Manager," (alone on its own line)
Second sentence MUST be exactly: "I am applying for the {job['job_title']} role at {job['company']}."
Then use the ATS_OPENING — the strongest possible hook, no throat-clearing.
State your most relevant qualification in one confident sentence.

PARAGRAPH 2 — Proof (4 sentences minimum):
Name the specific roles and companies where you built the relevant experience.
Reference 2–3 STRONG_MATCHES — exact achievements, not vague claims.
Use KEYWORD_TALKING_POINTS — embed the exact phrasing naturally.
No filler: "I am passionate about", "I am excited to", "I believe I would be a great fit".

PARAGRAPH 3 — Why here, why now (3 sentences minimum):
State specifically why this company and this role. Use UNIQUE_ANGLE.
Point to one concrete requirement from the JD that you directly match.

PARAGRAPH 4 — Closing (2 sentences):
Direct call to action — invite them to discuss. No over-polite hedging.
Sign off: "Best regards,\\n{candidate_name}"

{_modern_rules}
{no_think}""",
        agent=en_modern_writer,
        expected_output="Modern English cover letter body, exactly 4 paragraphs, plain text",
        context=[jd_task, gap_task, ats_task]
    )

    # Task 6 — Fact Checker (violation report only — no rewriting)
    fact_check_task = Task(
        description=f"""
You are a rigorous fact-checking editor for engineering cover letters.
Your SOLE job is to find claims that cannot be verified from the candidate profile.
You do NOT evaluate writing quality or style — only factual accuracy.

You receive:
1. Two cover letters: EN Formal (Task 4) and EN Modern (Task 5)
2. Candidate profile: Resume Analysis (Task 1)
3. PROHIBITED_CLAIMS and SAFE_FRAMING from the Skill Gap Map (Task 2)

For EACH letter, go sentence by sentence. For every claim about a skill, tool, domain,
job title, or standard, evaluate:
  Q1: Is this claim directly evidenced in the candidate profile? (YES/NO + quote the evidence)
  Q2: If NO — is it in PROHIBITED_CLAIMS? → HARD_VIOLATION
  Q3: If NO — is it an adjacent domain overclaim without using SAFE_FRAMING? → SOFT_VIOLATION
  Q4: Does the language strength match the evidence depth?
      ("expertise" needs multiple deep examples; "experience" needs one specific project;
       "exposure to" for single mentions; never stronger language than the evidence supports)
      If overclaiming strength → CALIBRATION issue

  AEROSPACE/UAS RULE — applies when AEROSPACE_FLAG is YES in Task 2:
  Any claim of "direct UAS experience", "unmanned systems experience", or "drone development"
  is a HARD_VIOLATION unless the profile explicitly names a UAS or drone project.
  Manned military aircraft roles (fighter jets, combat aircraft) are NOT UAS experience —
  do not pass them as equivalent even under SOFT_VIOLATION.
  If the candidate's aerospace role title was mechanical engineer or systems engineer (not software),
  any claim that this role constitutes "aerospace software development" or "avionics software" is
  a SOFT or HARD violation depending on how strongly it is stated.

Output EXACTLY this format (no preamble, no commentary):

EN_FORMAL_VIOLATIONS:
HARD: "[exact phrase from letter]" — reason → replace with: [exact SAFE_FRAMING sentence or state REMOVE]
SOFT: "[exact phrase from letter]" — reason → replace with: [weaker honest phrasing]
CALIBRATION: "[exact phrase from letter]" — overclaims strength → replace with: [calibrated version]
PASS: [N] sentences verified against profile ✓

EN_MODERN_VIOLATIONS:
HARD: "[exact phrase from letter]" — reason → replace with: [exact SAFE_FRAMING sentence or state REMOVE]
SOFT: "[exact phrase from letter]" — reason → replace with: [weaker honest phrasing]
CALIBRATION: "[exact phrase from letter]" — overclaims strength → replace with: [calibrated version]
PASS: [N] sentences verified against profile ✓

SUMMARY:
Total HARD violations: N
Total SOFT violations: N
Total CALIBRATION issues: N

If there are no violations of a type, write NONE for that type. Do not skip the section.
Do NOT rewrite any letter — only report violations with exact replacement phrases.
{no_think}""",
        agent=fact_checker,
        expected_output="Structured violation report: EN_FORMAL_VIOLATIONS and EN_MODERN_VIOLATIONS with HARD/SOFT/CALIBRATION/PASS sections, plus SUMMARY counts",
        context=[en_formal_task, en_modern_task, gap_task, resume_task]
    )

    # Task 7 — Tone + Grammar Review (final output)
    if small_model:
        _review_description = f"""
You have two cover letter drafts (EN Formal, EN Modern) and a VIOLATION REPORT from the Fact Checker.

Revise each letter, applying ALL of the following:

1. APPLY FACT-CHECK CORRECTIONS — fix every HARD, SOFT, and CALIBRATION violation in the
   VIOLATION REPORT (Task 6) exactly as specified. Do not argue with the report.
2. SALUTATION — each letter MUST begin with "Dear Hiring Manager," on the very first line.
3. ENGLISH ONLY — replace any German word with its English equivalent (technical proper
   nouns like AUTOSAR, DO-178C, MISRA C, CANoe, Simulink stay as-is).
4. BANNED PHRASES — remove and replace with specific concrete language:
   "I am passionate about", "I am excited to", "I am writing to express", "leverage",
   "I believe I would be a great fit", "dynamic team", "aligns perfectly",
   "With extensive experience in", "I am confident that", "I am well-suited".
5. PARAGRAPHS — each letter must have EXACTLY 4 paragraphs, each with at least 3 sentences.
   Expand thin paragraphs with concrete evidence from context (roles, companies, achievements).
6. SIGN-OFF — each letter must end with "{candidate_name}" on its own line:
   - EN Formal: "Mit freundlichen Grüßen / Kind regards,\\n{candidate_name}"
   - EN Modern: "Best regards,\\n{candidate_name}"
7. Fix grammar errors. Do not introduce new ones.

Output BOTH revised letters in EXACTLY this format (no extra text before or after):

EN_FORMAL_FINAL:
[revised formal English letter]

EN_MODERN_FINAL:
[revised modern English letter]
{no_think}"""
    else:
        _review_description = f"""
You have two cover letter drafts (EN Formal, EN Modern) and a VIOLATION REPORT from the Fact Checker.

Review each letter and fix ALL of the following IN ORDER:

1. APPLY FACT-CHECK CORRECTIONS — Read the VIOLATION REPORT from the Fact Checker (Task 6).
   Fix every HARD, SOFT, and CALIBRATION violation listed, in order:
   - HARD violations: remove the flagged phrase entirely or replace with the exact sentence given in the report.
   - SOFT violations: replace with the weaker honest phrasing given in the report.
   - CALIBRATION violations: replace with the calibrated version given in the report.
   Do not argue with the report — apply all fixes exactly as specified.
   If the SUMMARY shows "Total HARD violations: 0", skip this sub-step for that letter.

2. SALUTATION — each letter MUST begin with its salutation on the very first line:
   - EN Formal  → "Dear Hiring Manager,"
   - EN Modern  → "Dear Hiring Manager,"
   If any salutation is missing, add it as the very first line.

3. LANGUAGE CONSISTENCY — EN Formal and EN Modern:
   Remove EVERY German word that is NOT in this allowed technical proper-noun list:
   DO-178C, AUTOSAR, VxWorks, ASIL B, MISRA C, MIL/SIL/HIL, CANoe, WinIdea, Simulink,
   DOORS, FOC, IPMSM, MXAM, Polyspace, TESSY, A-SPICE, Eclipse.
   Replace with English: hardwarenahe→low-level, sicherheitskritisch→safety-critical,
   echtzeitfähig→real-time-capable, Embedded Entwicklung→embedded development,
   zeitkritisch→time-critical, Anschreiben→cover letter.

4. BANNED PHRASES — remove every instance and replace with specific, concrete language:
   "I am passionate about", "I am excited to", "I am writing to express", "leverage", "synergies",
   "I believe I would be a great fit", "dynamic team", "aligns perfectly", "align perfectly",
   "With extensive experience in", "further reinforces", "unique blend", "I am confident that",
   "I look forward to contributing", "diverse challenges", "logical next step", "I am well-suited".

5. GRAMMAR — fix any grammatical errors. Do not introduce new ones.

6. TECHNICAL TERMS — verify: AUTOSAR, ISO 26262, A-SPICE, MISRA C, DO-178C, FOC, IPMSM are used correctly.
   Do not change correct technical terms.

7. PARAGRAPH DEPTH — every letter must have EXACTLY 4 paragraphs, each with at least 3 sentences.
   If a paragraph has fewer than 3 sentences, expand it with specific evidence (job titles, company
   names, concrete achievements from context). No generic filler — real evidence only.

8. FACTUAL CHECK — Remove any invented claim about years of experience (e.g. "over ten years",
   "many years") unless supported by explicit dates in the letter content itself.
   Replace with the specific role and duration actually visible in the letter (e.g. "during my time at [company]").

9. SIGN-OFF COMPLETENESS — every letter must end with the full sign-off including "{candidate_name}"
   on its own line after the closing phrase. If the name is missing, add it.
   - EN Formal: "Mit freundlichen Grüßen / Kind regards,\\n{candidate_name}"
   - EN Modern: "Best regards,\\n{candidate_name}"

Output BOTH revised letters in EXACTLY this format (no extra text before or after):

EN_FORMAL_FINAL:
[revised formal English letter]

EN_MODERN_FINAL:
[revised modern English letter]
{no_think}"""

    review_task = Task(
        description=_review_description,
        agent=tone_grammar_reviewer,
        expected_output="Both revised letters labeled EN_FORMAL_FINAL: and EN_MODERN_FINAL:",
        context=[en_formal_task, en_modern_task, fact_check_task]
    )

    # ══════════════════════════════════════════
    #  CREW
    # ══════════════════════════════════════════

    crew = Crew(
        agents=[
            job_analyst,
            resume_analyzer,
            skill_gap_mapper,
            ats_optimizer,
            en_formal_writer,
            en_modern_writer,
            fact_checker,
            tone_grammar_reviewer,
        ],
        tasks=[
            jd_task,            # 0
            resume_task,        # 1
            gap_task,           # 2
            ats_task,           # 3
            en_formal_task,     # 4
            en_modern_task,     # 5
            fact_check_task,    # 6
            review_task,        # 7  ← final output
        ],
        verbose=False,
        # task_callback fires exactly once per completed task (8 total) — the
        # step_callback CrewAI offers fires per agent *step* and overcounts.
        task_callback=step_callback
    )

    return crew


# ─────────────────────────────────────────────
#  SAVE TO WORD (.docx)
# ─────────────────────────────────────────────

def _safe_part(s: str, max_len: int = 28) -> str:
    import re as _re
    words = _re.sub(r'[^\w\s]', '', s.strip()).split()
    return (''.join(w.capitalize() for w in words)[:max_len] or 'Unknown')


def _add_horizontal_rule(doc: "Document") -> None:
    """Add a thin grey horizontal line using paragraph bottom border."""
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after  = Pt(0)
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement('w:pBdr')
    bottom = OxmlElement('w:bottom')
    bottom.set(qn('w:val'),   'single')
    bottom.set(qn('w:sz'),    '4')
    bottom.set(qn('w:space'), '1')
    bottom.set(qn('w:color'), 'CCCCCC')
    pBdr.append(bottom)
    pPr.append(pBdr)


def _build_docx_doc(job: dict, variants: list,
                    candidate_name: str = "", candidate_address: str = "",
                    candidate_email: str = "", candidate_phone: str = "") -> "Document":
    """
    Builds a send-ready business letter Word document.
    Each variant gets its own page, formatted as a professional cover letter —
    no cover page, no 'VARIANT' headings, ready to print and send.

    `variants` is a list of (label, letter_text) tuples.
    """
    doc = Document()

    # European A4-friendly margins
    for section in doc.sections:
        section.top_margin    = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin   = Inches(1.25)
        section.right_margin  = Inches(1.0)

    style = doc.styles['Normal']
    style.font.name = 'Calibri'
    style.font.size = Pt(11)

    today = datetime.now().strftime("%d %B %Y")

    for i, (label, letter_text) in enumerate(variants):
        if i > 0:
            doc.add_page_break()

        # ── If multiple variants, add a subtle tab label at top ──────────
        if len(variants) > 1:
            lbl_p = doc.add_paragraph()
            lbl_r = lbl_p.add_run(label.upper())
            lbl_r.font.size  = Pt(8)
            lbl_r.font.color.rgb = RGBColor(0xAA, 0xAA, 0xAA)
            lbl_r.font.name  = 'Calibri'
            lbl_p.paragraph_format.space_after = Pt(4)

        # ── Sender block (right-aligned) ─────────────────────────────────
        def _rline(text: str, bold: bool = False, size: int = 10):
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            r = p.add_run(text)
            r.bold = bold
            r.font.size = Pt(size)
            r.font.name = 'Calibri'
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after  = Pt(1)

        if candidate_name:
            _rline(candidate_name, bold=True, size=12)
        contact = "  ·  ".join(filter(None, [candidate_phone, candidate_email]))
        if contact:
            _rline(contact, size=9)
        if candidate_address:
            _rline(candidate_address, size=9)

        # ── Thin divider ─────────────────────────────────────────────────
        _add_horizontal_rule(doc)
        _sp = doc.add_paragraph()
        _sp.paragraph_format.space_after = Pt(6)

        # ── Date (right-aligned) ─────────────────────────────────────────
        date_p = doc.add_paragraph()
        date_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        date_r = date_p.add_run(today)
        date_r.font.size = Pt(10)
        date_r.font.name = 'Calibri'
        date_p.paragraph_format.space_after = Pt(14)

        # ── Recipient block (left-aligned) ───────────────────────────────
        def _lline(text: str, bold: bool = False, size: int = 10):
            p = doc.add_paragraph()
            r = p.add_run(text)
            r.bold = bold
            r.font.size = Pt(size)
            r.font.name = 'Calibri'
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after  = Pt(1)

        _lline(job.get('company', ''), bold=True, size=11)
        if job.get('location'):
            _lline(job['location'], size=10)

        # ── Subject line ─────────────────────────────────────────────────
        subj_text = f"Re: Application — {job.get('job_title', '')}"
        if job.get('ref_number'):
            subj_text += f"  ·  Ref: {job['ref_number']}"

        doc.add_paragraph()
        subj_p = doc.add_paragraph()
        subj_r = subj_p.add_run(subj_text)
        subj_r.bold = True
        subj_r.font.size = Pt(11)
        subj_r.font.name = 'Calibri'
        subj_p.paragraph_format.space_after = Pt(14)

        # ── Letter body ──────────────────────────────────────────────────
        body = str(letter_text).strip() if letter_text else "[Letter generation failed]"
        first_para = True
        for para in body.split("\n"):
            para = para.strip()
            if not para:
                continue
            p = doc.add_paragraph(para)
            p.runs[0].font.name = 'Calibri'
            p.runs[0].font.size = Pt(11)
            p.paragraph_format.space_after  = Pt(8)
            p.paragraph_format.space_before = Pt(0)
            # Salutation and sign-off lines: left-aligned, not justified
            if first_para or para.startswith(("Kind regards", "Best regards",
                                               "Mit freundlichen", "Veuillez",
                                               "Atentamente", "Distinti",
                                               "Met vriendelijke")):
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            first_para = False

        # ── Signature space ──────────────────────────────────────────────
        sig_p = doc.add_paragraph()
        sig_p.paragraph_format.space_before = Pt(16)
        sig_p.paragraph_format.space_after  = Pt(0)
        if candidate_name:
            sig_r = sig_p.add_run(candidate_name)
            sig_r.font.name = 'Calibri'
            sig_r.font.size = Pt(11)

    return doc


def save_to_docx(job: dict, variants, output_dir: Path,
                 candidate_name: str = "", candidate_address: str = "",
                 candidate_email: str = "", candidate_phone: str = "") -> Path:
    """
    Save cover letter variants to disk.
    `variants` accepts:
      - list of (label, text) tuples  — new style
      - list of plain strings          — legacy: treated as (EN Formal, EN Modern)
    Filename: CandidateName_JobTitle_CompanyName_YYYYMMDD.docx
    """
    # Normalise legacy call (list of strings)
    if variants and isinstance(variants[0], str):
        _labels = ["English - Formal", "English - Modern", "English - Variant 3"]
        variants = [((_labels[i] if i < len(_labels) else f"Variant {i+1}"), t)
                    for i, t in enumerate(variants)]

    output_dir.mkdir(exist_ok=True)
    date_str     = datetime.now().strftime("%d%m%Y")
    name_part    = _safe_part(candidate_name or "Candidate")
    title_part   = _safe_part(job.get('job_title', 'Position'))
    company_part = _safe_part(job.get('company', 'Company'))
    filename     = output_dir / f"CoverLetter_{name_part}_{title_part}_{company_part}_{date_str}.docx"

    doc = _build_docx_doc(job, variants,
                          candidate_name=candidate_name,
                          candidate_address=candidate_address,
                          candidate_email=candidate_email,
                          candidate_phone=candidate_phone)
    doc.save(str(filename))
    return filename


def build_docx_bytes(job: dict, variants: list,
                     candidate_name: str = "", candidate_address: str = "",
                     candidate_email: str = "", candidate_phone: str = "") -> bytes:
    """
    Build a docx and return raw bytes (for Streamlit download_button).
    `variants` is a list of (label, letter_text) tuples.
    """
    import io
    doc = _build_docx_doc(job, variants,
                          candidate_name=candidate_name,
                          candidate_address=candidate_address,
                          candidate_email=candidate_email,
                          candidate_phone=candidate_phone)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ─────────────────────────────────────────────
#  TRANSLATION
# ─────────────────────────────────────────────

_LANG_REGISTER = {
    "German": (
        "You are a native German business writer with 15 years of experience "
        "writing Bewerbungsschreiben for engineering roles in Germany.\n\n"
        "SALUTATION: Always 'Sehr geehrte Damen und Herren,' — never any gendered slash form.\n"
        "SIGN-OFF: 'Mit freundlichen Grüßen' only. No bilingual sign-offs.\n\n"
        "CLOSING PARAGRAPH — strict rule:\n"
        "  - Write EXACTLY ONE closing call-to-action sentence. Not two, not three.\n"
        "  - Pick one idea: either availability for interview OR looking forward to reply — never both.\n"
        "  - WRONG: 'Ich freue mich...Ich stehe gerne zur Verfügung...Bitte kontaktieren Sie mich...'\n"
        "  - RIGHT:  'Über eine Einladung zu einem persönlichen Gespräch würde ich mich sehr freuen.'\n\n"
        "TONE: Factual, modest, structured — NOT American self-marketing.\n"
        "  - AVOID: 'qualifiziert mich als starken Kandidaten' → "
        "PREFER: 'deckt sich gut mit den Anforderungen dieser Position'\n"
        "  - AVOID: repeating the same qualification idea twice in one paragraph.\n\n"
        "SENTENCE STRUCTURE — avoid translation artifacts:\n"
        "  - Do NOT start two consecutive paragraphs with the same word (e.g. 'Meine Arbeit...' twice).\n"
        "  - Break long English-style clauses into shorter German sentences.\n"
        "  - Restructure phrases that sound like translated English into natural German word order.\n"
        "  - EXAMPLE of translation artifact to fix:\n"
        "      English: 'establishes my qualification for this position'\n"
        "      Bad German: 'belegen meine Eignung für diese Position' (if already used once)\n"
        "      Fix: omit the repetition entirely or rephrase as 'passt zu den Anforderungen'\n\n"
        "TERMINOLOGY — use native German engineering vocabulary:\n"
        "  - 'Embedded-Softwareentwicklung' (NOT 'Entwicklung eingebetteter Software')\n"
        "  - 'Seriencode-Integration' (NOT 'Produktionscode-Integration')\n"
        "  - 'Analyse des Systemverhaltens' (NOT 'Verhaltensanalyse' alone)\n"
        "  - Use established German compound nouns — never literal word-for-word constructions.\n\n"
        "HONEST FRAMING: Keep phrases like 'ein Bereich, in den ich mich gerade einarbeite' — "
        "German recruiters value this honesty."
    ),
    "French": (
        "You are a native French business writer for professional job applications.\n"
        "Use formal 'vous' (vouvoiement) throughout.\n"
        "Sign off with 'Veuillez agréer, Madame, Monsieur, l'expression de mes salutations distinguées.'\n"
        "CLOSING: One closing sentence only — no repetition of the call-to-action.\n"
        "STYLE: Avoid literal English sentence structures. Rephrase into natural French flow. "
        "French professional letters are formal but not verbose — avoid filler phrases."
    ),
    "Spanish": (
        "You are a native Spanish business writer for professional job applications.\n"
        "Use formal 'usted' throughout. Sign off with 'Atentamente,'.\n"
        "CLOSING: One closing call-to-action only.\n"
        "STYLE: Avoid literal English constructions. Use natural Spanish business phrasing."
    ),
    "Italian": (
        "You are a native Italian business writer for professional job applications.\n"
        "Use formal 'Lei' throughout. Sign off with 'Distinti saluti,'.\n"
        "CLOSING: One closing sentence only.\n"
        "STYLE: Avoid literal English constructions. Use natural Italian business phrasing."
    ),
    "Dutch": (
        "You are a native Dutch business writer for professional job applications.\n"
        "Use formal 'u' throughout. Sign off with 'Met vriendelijke groet,'.\n"
        "CLOSING: One closing sentence only.\n"
        "STYLE: Avoid literal English constructions. Use natural Dutch business phrasing."
    ),
    "Portuguese": (
        "You are a native Portuguese business writer for professional job applications.\n"
        "Use formal register throughout. Sign off with 'Atenciosamente,'.\n"
        "CLOSING: One closing sentence only.\n"
        "STYLE: Avoid literal English constructions. Use natural Portuguese business phrasing."
    ),
    "Polish": (
        "You are a native Polish business writer for professional job applications.\n"
        "Use formal address throughout. Sign off with 'Z poważaniem,'.\n"
        "CLOSING: One closing sentence only.\n"
        "STYLE: Avoid literal English constructions. Use natural Polish business phrasing."
    ),
    "Swedish": (
        "You are a native Swedish business writer for professional job applications.\n"
        "Use formal address throughout. Sign off with 'Med vänliga hälsningar,'.\n"
        "CLOSING: One closing sentence only.\n"
        "STYLE: Avoid literal English constructions. Use natural Swedish business phrasing."
    ),
}

_PRESERVED_TERMS = (
    "DO-178C, AUTOSAR, VxWorks, ASIL, MISRA C, MIL/SIL/HIL, CANoe, WinIdea, "
    "Simulink, DOORS, FOC, IPMSM, MXAM, Polyspace, TESSY, A-SPICE"
)


def translate_letter(text: str, target_lang: str,
                     llm_config: dict | None = None) -> str:
    """
    Translate a cover letter to target_lang using the configured LLM.
    Preserves proper nouns, technical standards, and company/job names.
    """
    llm = get_llm(llm_config)
    register = _LANG_REGISTER.get(target_lang, "Use formal register throughout.")
    prompt = (
        f"You are a native {target_lang} professional writer. "
        f"Your task is to rewrite the cover letter below so that it reads exactly as a "
        f"native {target_lang} speaker would write it from scratch — NOT as a translation.\n\n"
        f"This means:\n"
        f"- Restructure sentences where needed so they sound natural in {target_lang}.\n"
        f"- Replace English-style phrasing with the idiomatic {target_lang} equivalent.\n"
        f"- Never carry over English word order, clause structure, or repeated phrases.\n"
        f"- If the source letter repeats an idea (e.g. two closing calls-to-action), keep only one.\n\n"
        f"Writer persona and style rules:\n"
        f"{register}\n\n"
        f"Content rules (non-negotiable):\n"
        f"- Preserve ALL of the following exactly as written (do NOT translate or alter): "
        f"company names, candidate name, job titles, "
        f"technical terms and standards ({_PRESERVED_TERMS}).\n"
        f"- Keep all factual content and the same paragraph structure.\n"
        f"- Do NOT add or remove any information.\n"
        f"- Output ONLY the rewritten letter text, nothing else.\n\n"
        f"LETTER TO REWRITE:\n{text}\n{_no_think_suffix(llm_config)}"
    )
    result = llm.call([{"role": "user", "content": prompt}])
    return _strip_think(result if isinstance(result, str) else str(result))


# ─────────────────────────────────────────────
#  MATCH SCORE
# ─────────────────────────────────────────────

def _compute_match_score(gap_raw: str) -> int:
    """
    Derive a 0–100 match score from the Skill Gap Mapper output (task 2).
    Formula: (strong + 0.5 * partial) / (strong + partial + gaps) * 100
    """
    import re as _re

    def _count(header: str) -> int:
        # \Z (not $): with (?m) active, $ matches every line end, which would
        # cut each section down to its first line and skew the score.
        m = _re.search(
            rf'(?m)^{header}:\s*\n(.*?)(?=\n[A-Z_]{{3,}}:|\Z)',
            gap_raw, _re.S
        )
        if not m:
            return 0
        return sum(
            1 for ln in m.group(1).splitlines()
            if ln.strip() and not ln.strip().startswith('#')
        )

    strong  = _count("STRONG_MATCHES")
    partial = _count("PARTIAL_MATCHES")
    gaps    = _count("PROHIBITED_CLAIMS")
    total   = strong + partial + gaps
    if total == 0:
        return 0
    return round((strong + 0.5 * partial) / total * 100)


# ─────────────────────────────────────────────
#  LETTER SANITATION + VALIDATION  (deterministic guard rails)
# ─────────────────────────────────────────────
#  Small local models drift: markdown fences, leftover labels, thinking blocks,
#  missing sign-offs. These checks are cheap Python — catching problems here is
#  far more reliable than asking a 7-8B model to police itself.

_BANNED_PHRASES = [
    "I am passionate about", "I am excited to", "I am writing to express",
    "I believe I would be a great fit", "dynamic team", "aligns perfectly",
    "align perfectly", "With extensive experience in", "further reinforces",
    "unique blend", "I am confident that", "I look forward to contributing",
    "diverse challenges", "logical next step", "I am well-suited",
]


def _sanitize_letter(text: str) -> str:
    """Deterministically strip non-letter artifacts from model output."""
    t = _strip_think(text or "")
    t = re.sub(r'```[a-zA-Z]*', '', t).replace('```', '')
    # leftover pipeline labels the model may echo (handle brackets, spaces, variants)
    t = re.sub(r'(?im)^\s*\[?\s*EN_?(FORMAL|MODERN)_?(FINAL|DRAFT)?\s*\]?\s*:?\s*', '', t)
    # markdown headers / bold markers
    t = re.sub(r'(?m)^#{1,6}\s*', '', t)
    t = t.replace('**', '')
    return t.strip()


def _validate_letter(text: str, candidate_name: str = "") -> list[str]:
    """Return a list of concrete problems (empty list = letter is acceptable)."""
    issues: list[str] = []
    if not text or len(text.strip()) < 200:
        issues.append("the letter is empty or far too short")
        return issues
    if not re.search(r'(?im)^dear\b', text):
        issues.append('the salutation line (e.g. "Dear Hiring Manager,") is missing')
    paragraphs = [p for p in re.split(r'\n\s*\n', text) if p.strip()]
    if len(paragraphs) < 4:
        issues.append(f"only {len(paragraphs)} paragraphs — a salutation, at least "
                      "3-4 body paragraphs, and a sign-off are required")
    if candidate_name and candidate_name.lower() not in text.lower():
        issues.append(f'the sign-off must include the candidate name "{candidate_name}"')
    found = [p for p in _BANNED_PHRASES if p.lower() in text.lower()]
    if found:
        issues.append("these banned filler phrases must be replaced with specific, "
                      "concrete language: " + "; ".join(f'"{p}"' for p in found))
    if "<think" in text.lower():
        issues.append("internal reasoning text leaked into the letter — remove it")
    return issues


def _repair_letter(text: str, issues: list[str], llm: LLM, no_think: str = "") -> str:
    """One targeted single-shot repair call — fix listed issues, change nothing else."""
    issue_list = "\n".join(f"- {i}" for i in issues)
    prompt = (
        "Below is a cover letter with specific problems. Fix ONLY the listed problems. "
        "Do not change any other wording, facts, or structure. "
        "Output ONLY the corrected letter — no commentary, no markdown.\n\n"
        f"PROBLEMS TO FIX:\n{issue_list}\n\n"
        f"LETTER:\n{text}\n{no_think}"
    )
    try:
        result = llm.call([{"role": "user", "content": prompt}])
        if result is None:
            return ""
        return _sanitize_letter(result if isinstance(result, str) else str(result))
    except Exception:
        return ""


def _finalize_letter(reviewed_text: str, writer_raw: str,
                     candidate_name: str, llm: LLM, no_think: str = "") -> str:
    """
    Pick the best available letter: reviewer output → validated;
    on validation failure make ONE repair attempt; keep whichever
    version has the fewest remaining issues (reviewer > writer raw).
    """
    candidate = _sanitize_letter(reviewed_text) or _sanitize_letter(writer_raw)
    if not candidate:
        return ""
    issues = _validate_letter(candidate, candidate_name)
    if not issues:
        return candidate
    repaired = _repair_letter(candidate, issues, llm, no_think)
    if repaired and len(_validate_letter(repaired, candidate_name)) < len(issues):
        return repaired
    return candidate


# ─────────────────────────────────────────────
#  QUICK MATCH CHECK  (pre-generation, single LLM call)
# ─────────────────────────────────────────────

def quick_match_check(profile_text: str, job_raw: str,
                      llm_config: dict | None = None,
                      company_override: str | None = None) -> dict:
    """
    Fast pre-generation match analysis — single LLM call, no crew.
    Extracts job metadata AND match assessment in one shot.

    Returns:
        match_score   : int 0–100
        strong        : list[str]  — up to 3 strong match points
        gaps          : list[str]  — up to 3 gap / weak areas
        job           : dict       — {company, job_title, location, ref_number}
    """
    import re as _re

    # Structured extraction — low temperature is far more reliable on small models.
    _analysis_config = {**llm_config, "temperature": 0.2} if llm_config else None
    llm = get_llm(_analysis_config)
    _nt = _no_think_suffix(llm_config)

    prompt = (
        "You will receive a job posting and a candidate profile.\n"
        "Output EXACTLY the following lines — no extra text, no markdown:\n\n"
        "JOB_TITLE: <job title extracted from posting>\n"
        "COMPANY: <company name extracted from posting>\n"
        "LOCATION: <city/country or NONE>\n"
        "REF: <reference or job number or NONE>\n"
        "MATCH_SCORE: <integer 0-100 — how well profile matches job>\n"
        "STRONG_1: <specific matching skill or experience — one sentence>\n"
        "STRONG_2: <specific matching skill or experience — one sentence>\n"
        "STRONG_3: <specific matching skill or experience — one sentence>\n"
        "GAP_1: <specific gap or missing requirement — one sentence>\n"
        "GAP_2: <specific gap or missing requirement — one sentence>\n"
        "GAP_3: <specific gap or missing requirement — one sentence>\n\n"
        f"JOB POSTING:\n{job_raw[:6000]}\n\n"
        f"CANDIDATE PROFILE:\n{profile_text[:6000]}\n{_nt}"
    )

    raw = llm.call([{"role": "user", "content": prompt}])
    raw = _strip_think(raw if isinstance(raw, str) else str(raw))

    def _pick(key: str) -> str:
        m = _re.search(rf'(?m)^{key}:\s*(.+)$', raw)
        v = m.group(1).strip() if m else ""
        return "" if v.upper() in ("NONE", "N/A", "-") else v

    score_str = _pick("MATCH_SCORE")
    try:
        score = max(0, min(100, int(_re.sub(r'[^\d]', '', score_str))))
    except (ValueError, TypeError):
        score = 0

    strong = [s for s in [_pick("STRONG_1"), _pick("STRONG_2"), _pick("STRONG_3")] if s]
    gaps   = [s for s in [_pick("GAP_1"),    _pick("GAP_2"),    _pick("GAP_3")]    if s]

    job = {
        "company":    company_override or _pick("COMPANY"),
        "job_title":  _pick("JOB_TITLE"),
        "location":   _pick("LOCATION"),
        "ref_number": _pick("REF"),
    }

    return {
        "match_score": score,
        "strong":      strong,
        "gaps":        gaps,
        "job":         job,
    }


# ─────────────────────────────────────────────
#  PUBLIC API (for Streamlit and other callers)
# ─────────────────────────────────────────────

def generate_cover_letters(
    profile_text: str,
    job_raw: str,
    llm_config: dict | None = None,
    pre_parsed_job: dict | None = None,
    step_callback=None,
    save_docx: bool = False,
    output_dir: Path | None = None,
    company_override: str | None = None,
    candidate_name: str = "",
    candidate_address: str = "",
    candidate_email: str = "",
    candidate_phone: str = "",
    structured_profile: dict | None = None,
    industry: str = "Generic",
) -> dict:
    """
    High-level entry point for non-CLI callers (Streamlit, tests, etc.).

    pre_parsed_job     : supply an already-verified job dict to skip the parser agent.
    structured_profile : pre-parsed profile dict from parse_profile(); parsed here if None.

    Returns dict with keys:
        en_formal, en_modern : str  — letter body text
        job                  : dict — parsed job metadata
        docx_path            : Path | None
        profile_parsed       : dict — structured profile (for caching)
    """
    # Writer LLM keeps the user's temperature; analysis tasks run at low
    # temperature (structured output drifts badly at 0.7 on small models).
    llm = get_llm(llm_config)
    _analysis_config = {**llm_config, "temperature": 0.2} if llm_config else None
    analysis_llm = get_llm(_analysis_config) if llm_config else llm
    _no_think    = _no_think_suffix(llm_config)
    _small       = _is_small_local_model(llm_config)
    # For Ollama, the single-shot extraction calls can use server-side JSON mode.
    _is_ollama = bool(llm_config) and llm_config.get("backend") == "ollama"
    json_llm   = get_llm(_analysis_config, json_mode=True) if _is_ollama else analysis_llm

    if pre_parsed_job is not None:
        job = {**pre_parsed_job}
        if not job.get("job_desc"):
            job["job_desc"] = job_raw
    else:
        job = clean_job_paste(job_raw, json_llm, interactive=False, no_think=_no_think)

    if company_override:
        job["company"] = company_override

    # Parse profile if not already cached
    if structured_profile is None:
        structured_profile = parse_profile(profile_text, json_llm, no_think=_no_think)

    _cname = candidate_name.strip() or (structured_profile.get("name") if "name" in structured_profile else "") or _extract_name_from_profile(profile_text)
    resolved_industry = _resolve_industry(industry, job)
    crew = build_crew(job, llm, profile_text=profile_text, step_callback=step_callback,
                      candidate_name=_cname or "the candidate",
                      structured_profile=structured_profile,
                      industry=resolved_industry,
                      analysis_llm=analysis_llm,
                      small_model=_small,
                      no_think=_no_think)
    tasks_output = crew.kickoff()
    outputs = tasks_output.tasks_output

    # Task index map: 0=jd, 1=resume, 2=gap, 3=ats, 4=en_formal, 5=en_modern,
    #                 6=fact_check, 7=review (final)
    #
    # Primary: parse the reviewer's combined output (task 7)
    # Fallback: raw writer outputs (tasks 4-5); each letter is then sanitized,
    # validated, and — if needed — repaired with one targeted LLM call.
    reviewed = {}
    if len(outputs) > 7 and outputs[7].raw:
        reviewed = _parse_reviewer_output(outputs[7].raw)

    letters = [
        _finalize_letter(reviewed.get("en_formal", ""),
                         outputs[4].raw if len(outputs) > 4 else "",
                         _cname, llm, _no_think),
        _finalize_letter(reviewed.get("en_modern", ""),
                         outputs[5].raw if len(outputs) > 5 else "",
                         _cname, llm, _no_think),
    ]

    gap_raw     = outputs[2].raw if len(outputs) > 2 else ""
    match_score = _compute_match_score(gap_raw) if gap_raw else None

    docx_path = None
    if save_docx:
        _dir = output_dir or (Path(__file__).parent / "output")
        _variants = [
            ("English - Formal", letters[0]),
            ("English - Modern", letters[1]),
        ]
        docx_path = save_to_docx(job, _variants, _dir,
                                  candidate_name=_cname,
                                  candidate_address=candidate_address,
                                  candidate_email=candidate_email,
                                  candidate_phone=candidate_phone)

    return {
        "en_formal":      letters[0],
        "en_modern":      letters[1],
        "job":            job,
        "docx_path":      docx_path,
        "profile_parsed": structured_profile,
        "match_score":    match_score,
        "industry":       resolved_industry,
    }


# ─────────────────────────────────────────────
#  CLI ENTRY POINT
# ─────────────────────────────────────────────

def main():
    llm = get_llm()
    raw = collect_raw_paste()
    print("\nCleaning job page — extracting structured info...\n")
    job = clean_job_paste(raw, llm, interactive=True)

    print(f"\nRunning crew for: {job['job_title']} @ {job['company']}")
    print("   This takes 1-3 minutes depending on model...\n")

    crew = build_crew(job, llm)
    tasks_output = crew.kickoff()
    task_outputs = tasks_output.tasks_output

    reviewed = _parse_reviewer_output(task_outputs[7].raw) if len(task_outputs) > 7 else {}
    _cli_name = _extract_name_from_profile(CANDIDATE_PROFILE)
    letter_results = [
        _finalize_letter(reviewed.get("en_formal", ""),
                         task_outputs[4].raw if len(task_outputs) > 4 else "", _cli_name, llm),
        _finalize_letter(reviewed.get("en_modern", ""),
                         task_outputs[5].raw if len(task_outputs) > 5 else "", _cli_name, llm),
    ]

    output_dir = Path(__file__).parent / "output"
    saved_path = save_to_docx(job, letter_results, output_dir)

    print("\n" + "="*60)
    print(f"Done! 2 English cover letters saved to:")
    print(f"   {saved_path}")
    print("="*60)
    print("\nVariants inside:")
    print("  1. English - Formal")
    print("  2. English - Modern")


if __name__ == "__main__":
    main()
