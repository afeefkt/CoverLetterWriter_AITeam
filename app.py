"""
Cover Letter Crew — Streamlit UI
Run with: streamlit run app.py
"""

import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
import streamlit as st

# Ensure this file's directory is on sys.path so imports work regardless of cwd
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# Load .env so API keys are visible to os.getenv() in the sidebar
load_dotenv(_HERE / ".env")

st.set_page_config(
    page_title="Cover Letter Crew",
    page_icon="✉",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
#  SESSION STATE DEFAULTS
# ─────────────────────────────────────────────

_LANGUAGES = [
    "English", "German", "French", "Spanish",
    "Italian", "Dutch", "Portuguese", "Polish", "Swedish",
]

_DEFAULTS = {
    "profile_text":      "",
    "results":           None,
    "is_running":        False,
    "run_error":         None,
    "job_meta":          None,
    "files_processed":   [],
    "candidate_name":    "",
    "candidate_address": "",
    "candidate_email":   "",
    "candidate_phone":   "",
    "employer_name":     "",
    "profile_parsed":    None,
    "profile_cache_text": "",
    "trans_formal":      None,
    "trans_modern":      None,
    "trans_lang":        None,
}
for _k, _v in _DEFAULTS.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def _extract_candidate_name(text: str, filename: str = "") -> str:
    """
    Multi-strategy regex name extraction — no LLM cost.
    Strategy 1: explicit label like 'Name: Jane Smith'
    Strategy 2: filename like 'Jane_Smith_CV.pdf'
    Strategy 3: first 15 lines for a standalone title-cased full name
    """
    # Strategy 1 — explicit label
    m = re.search(r'(?i)(?:full\s+)?name\s*[:\-]\s*([A-Z][a-zA-ZÀ-ÿ]+(?:\s[A-Z][a-zA-ZÀ-ÿ]+){1,3})', text)
    if m:
        return m.group(1).strip()

    # Strategy 2 — filename hint (strip extension + separators)
    if filename:
        stem = re.sub(r'\.(pdf|docx?|txt)$', '', filename, flags=re.I)
        stem = re.sub(r'[_\-\.]+', ' ', stem)
        stem = re.sub(r'(?i)(cv|resume|lebenslauf|bewerbung|application|\d+)', '', stem).strip()
        if re.match(r'^[A-Z][a-zA-ZÀ-ÿ]+(\s[A-Z][a-zA-ZÀ-ÿ]+){1,3}$', stem):
            return stem

    # Strategy 3 — standalone title-cased line in first 15 non-empty lines
    for line in text.strip().splitlines()[:15]:
        line = line.strip()
        if 4 < len(line) < 50 and re.match(r'^[A-Z][a-zA-ZÀ-ÿ]+(\s[A-Z][a-zA-ZÀ-ÿ]+){1,3}$', line):
            return line

    return ""


def _extract_company_regex(text: str) -> str:
    """Quick regex scan for company name patterns in a job posting."""
    patterns = [
        r'(?i)^company[:\s]+(.+)$',
        r'(?i)^employer[:\s]+(.+)$',
        r'(?i)^arbeitgeber[:\s]+(.+)$',
        r'(?i)·\s*([A-Z][^\n·]{2,40})\s*·',
    ]
    for pat in patterns:
        m = re.search(pat, text, re.MULTILINE)
        if m:
            val = m.group(1).strip().rstrip('.,')
            if val.lower() not in ('', 'our client', 'unser kunde'):
                return val
    return ""


def _llm_extract_name(profile_text: str, llm_config: dict) -> str:
    """Single-shot LLM call to extract candidate name — no crew overhead."""
    try:
        from cover_letter_crew import get_llm
        llm = get_llm(llm_config)
        result = llm.call([{
            "role": "user",
            "content": (
                "Extract the candidate's full name from this CV. "
                "Return ONLY the name, nothing else.\n\n"
                f"{profile_text[:2000]}\n\nFull name:"
            ),
        }])
        name = result.strip().strip('"\'').strip()
        if re.match(r'^[A-Z][a-z]+(\s[A-Z][a-zÀ-ÿ]+){1,3}$', name):
            return name
    except Exception:
        pass
    return ""


def _llm_extract_company(job_raw: str, llm_config: dict) -> str:
    """Single-shot LLM call to extract employer name from a job posting."""
    try:
        from cover_letter_crew import get_llm
        llm = get_llm(llm_config)
        result = llm.call([{
            "role": "user",
            "content": (
                "Extract the employer / company name from this job posting. "
                "Return ONLY the company name, nothing else. "
                "If it is anonymous, return exactly: unknown\n\n"
                f"{job_raw[:3000]}\n\nCompany name:"
            ),
        }])
        val = result.strip().strip('"\'').strip()
        if val.lower() not in ('unknown', '', 'our client', 'unser kunde'):
            return val
    except Exception:
        pass
    return ""


def _load_default_profile():
    """Load profile.py content on first run if text area is empty."""
    if st.session_state.profile_text:
        return
    try:
        from profile import CANDIDATE_PROFILE
        if CANDIDATE_PROFILE.strip():
            st.session_state.profile_text = CANDIDATE_PROFILE.strip()
    except ImportError:
        pass


def _save_profile_to_file(text: str, candidate_name: str = ""):
    profile_path = _HERE / "profile.py"
    escaped = text.replace('"""', r'\"\"\"')
    header = f"{candidate_name} — Candidate Profile" if candidate_name else "Candidate Profile"
    content = (
        f'"""\n{header}\n"""\n\n'
        f'CANDIDATE_PROFILE = """\n{escaped}\n"""\n'
    )
    profile_path.write_text(content, encoding="utf-8")


def _render_match_circle(score: int):
    """Render a circular SVG match-score gauge."""
    r     = 52
    circ  = 2 * 3.14159265 * r   # ≈ 326.7
    arc   = circ * score / 100
    # always green — deeper green for higher scores
    if score >= 70:
        color, label = "#16a34a", "Strong match"
    elif score >= 45:
        color, label = "#22c55e", "Good match"
    else:
        color, label = "#4ade80", "Partial match"

    st.markdown(f"""
<div style="display:flex;flex-direction:column;align-items:center;
            padding:12px 0 4px;gap:4px;">
  <svg width="140" height="140" viewBox="0 0 140 140">
    <!-- track -->
    <circle cx="70" cy="70" r="{r}" fill="none"
            stroke="#e5e7eb" stroke-width="13"/>
    <!-- filled arc -->
    <circle cx="70" cy="70" r="{r}" fill="none"
            stroke="{color}" stroke-width="13"
            stroke-linecap="round"
            stroke-dasharray="{arc:.1f} {circ:.1f}"
            transform="rotate(-90 70 70)"/>
    <!-- percentage number -->
    <text x="70" y="66" text-anchor="middle"
          font-size="28" font-weight="700" fill="{color}"
          font-family="sans-serif">{score}%</text>
    <!-- sub-label -->
    <text x="70" y="88" text-anchor="middle"
          font-size="11" fill="#9ca3af"
          font-family="sans-serif">match</text>
  </svg>
  <span style="font-size:13px;font-weight:600;color:{color};">{label}</span>
  <span style="font-size:11px;color:#9ca3af;">Job ↔ Profile fit</span>
</div>
""", unsafe_allow_html=True)


def _run_crew(profile_text: str, job_raw: str, llm_config: dict,
              company_override: str | None = None,
              candidate_name: str = "", candidate_address: str = "",
              candidate_email: str = "", candidate_phone: str = "",
              structured_profile=None, industry: str = "Generic"):
    """Execute the 8-task pipeline with a live heartbeat progress panel."""
    import time
    from cover_letter_crew import generate_cover_letters

    STEPS = [
        ("Job Analyst",            "Extracting requirements & keywords from JD"),
        ("Resume Analyzer",        "Reading your CV — skills & experience"),
        ("Skill Gap Mapper",       "Matching your profile to the job + setting truth boundaries"),
        ("ATS Optimizer",          "Building ATS keyword talking points"),
        ("EN Formal Writer",       "Drafting formal English letter"),
        ("EN Modern Writer",       "Drafting modern English letter"),
        ("Fact Checker",           "Verifying claims against your profile — removing hallucinations"),
        ("Tone & Grammar Reviewer","Applying fact-check fixes + polishing 2 letters"),
    ]
    N          = len(STEPS)
    start      = time.time()
    done_times = []       # seconds each completed step took
    step_idx   = [0]

    def _render(current: int) -> str:
        total = time.time() - start
        lines = [f"**{total:.0f}s elapsed** &nbsp;|&nbsp; step {current} / {N}\n"]
        for i, (name, desc) in enumerate(STEPS):
            if i < current:
                t = done_times[i] if i < len(done_times) else 0
                lines.append(f"✅ &nbsp;**{name}** — {desc} &nbsp;*(took {t:.0f}s)*")
            elif i == current and current < N:
                so_far = time.time() - start - sum(done_times)
                lines.append(f"⟳ &nbsp;**{name}** — {desc} &nbsp;*(running… {so_far:.0f}s)*")
            else:
                lines.append(f"○ &nbsp;{name} — {desc}")
        return "\n\n".join(lines)

    with st.status("Running 8-step crew — do not close this tab", expanded=True) as status:
        bar      = st.progress(0, text="Starting pipeline…")
        pipeline = st.empty()
        pipeline.markdown(_render(0))

        def step_cb(output):
            i         = step_idx[0]
            took      = time.time() - start - sum(done_times)
            done_times.append(max(took, 0.0))
            step_idx[0] = i + 1
            pct       = min((i + 1) / N, 1.0)
            nxt       = STEPS[i + 1][0] if i + 1 < N else "Finishing…"
            bar.progress(pct, text=f"Step {i + 1}/{N} done — next: {nxt}")
            pipeline.markdown(_render(i + 1))

        try:
            results = generate_cover_letters(
                profile_text=profile_text,
                job_raw=job_raw,
                llm_config=llm_config,
                step_callback=step_cb,
                save_docx=True,
                output_dir=_HERE / "output",
                company_override=company_override,
                candidate_name=candidate_name,
                candidate_address=candidate_address,
                candidate_email=candidate_email,
                candidate_phone=candidate_phone,
                structured_profile=structured_profile,
                industry=industry,
            )
            total = time.time() - start
            bar.progress(1.0, text=f"All {N} agents done in {total:.0f}s")
            pipeline.markdown(_render(N))
            st.session_state.results          = results
            st.session_state.job_meta         = results["job"]
            st.session_state.run_error        = None
            st.session_state.trans_formal     = None
            st.session_state.trans_modern     = None
            st.session_state.trans_lang       = None
            if results.get("profile_parsed"):
                st.session_state.profile_parsed    = results["profile_parsed"]
                st.session_state.profile_cache_text = profile_text
            status.update(label=f"Done! 2 English cover letters generated in {total:.0f}s", state="complete")
        except Exception as exc:
            st.session_state.run_error = str(exc)
            status.update(label=f"Failed: {exc}", state="error")
        finally:
            st.session_state.is_running = False

    st.rerun()


# ─────────────────────────────────────────────
#  BACKEND CATALOGUE
# ─────────────────────────────────────────────

_BACKENDS = {
    "Ollama (Local — Free)": {
        "id":      "ollama",
        "cost":    "Free — runs on your machine",
        "env_var": None,
        "models":  ["qwen2.5:7b", "qwen2.5:14b", "llama3.2:3b", "llama3.1:8b",
                    "mistral:7b", "gemma2:9b", "phi3:mini",
                    "deepseek-r1:7b", "deepseek-r1:8b", "deepseek-r1:14b"],
        "default": "qwen2.5:7b",
    },
    "DeepSeek (API)": {
        "id":      "deepseek",
        "cost":    "~$0.002 per full run",
        "env_var": "DEEPSEEK_API_KEY",
        "models":  ["deepseek-chat", "deepseek-reasoner"],
        "default": "deepseek-chat",
    },
    "Claude (Anthropic)": {
        "id":      "anthropic",
        "cost":    "Haiku ~$0.01 · Sonnet ~$0.05 · Opus ~$0.20",
        "env_var": "ANTHROPIC_API_KEY",
        "models":  ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-7"],
        "default": "claude-haiku-4-5",
    },
    "ChatGPT (OpenAI)": {
        "id":      "openai",
        "cost":    "4o-mini ~$0.01 · 4o ~$0.05",
        "env_var": "OPENAI_API_KEY",
        "models":  ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
        "default": "gpt-4o-mini",
    },
    "Gemini (Google)": {
        "id":      "gemini",
        "cost":    "Flash ~$0.01 · Pro ~$0.05 (new accounts: 2.5 models only)",
        "env_var": "GOOGLE_API_KEY",
        "models":  ["gemini-2.5-flash", "gemini-2.5-pro",
                    "gemini-2.5-flash-preview-05-20", "gemini-2.5-pro-preview-05-06"],
        "default": "gemini-2.5-flash",
    },
    "Groq (Fast & Free tier)": {
        "id":      "groq",
        "cost":    "Free tier available — very fast inference",
        "env_var": "GROQ_API_KEY",
        "models":  ["llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                    "mixtral-8x7b-32768", "gemma2-9b-it"],
        "default": "llama-3.3-70b-versatile",
    },
    "OpenRouter (100+ models)": {
        "id":      "openrouter",
        "cost":    "Varies — many free models available",
        "env_var": "OPENROUTER_API_KEY",
        "models":  ["meta-llama/llama-3.1-8b-instruct:free",
                    "google/gemma-2-9b-it:free",
                    "mistralai/mistral-7b-instruct:free",
                    "anthropic/claude-3.5-haiku",
                    "openai/gpt-4o-mini"],
        "default": "meta-llama/llama-3.1-8b-instruct:free",
    },
}

_BACKEND_LABELS = list(_BACKENDS.keys())


# ─────────────────────────────────────────────
#  SIDEBAR — LLM SETTINGS
# ─────────────────────────────────────────────

with st.sidebar:
    st.header("LLM Settings")

    chosen_label = st.selectbox(
        "Provider",
        _BACKEND_LABELS,
        index=0,
        help="Choose which AI service to use. Ollama runs free and locally; cloud APIs are faster.",
    )
    cfg = _BACKENDS[chosen_label]
    backend = cfg["id"]

    # Cost hint
    st.caption(f"Cost: {cfg['cost']}")

    # ── Model selector ───────────────────────
    preset_models = cfg["models"] + ["Custom…"]
    chosen_model = st.selectbox(
        "Model",
        preset_models,
        index=0,
        key=f"model_select_{backend}",
    )
    if chosen_model == "Custom…":
        model = st.text_input(
            "Custom model name",
            value=cfg["default"],
            key=f"model_custom_{backend}",
        )
    else:
        model = chosen_model

    # ── Backend-specific fields ──────────────
    if backend == "ollama":
        base_url = st.text_input(
            "Ollama URL",
            value="http://localhost:11434",
            help="Leave as-is unless Ollama runs on a different host/port.",
        )
        api_key = None
    else:
        env_var = cfg["env_var"] or ""
        api_key = os.getenv(env_var, "") if env_var else ""
        if api_key:
            st.success(f"API key loaded from .env ({env_var})", icon="✅")
        else:
            st.warning(
                f"No API key found. Add this to your `.env` file:\n\n"
                f"`{env_var}=your-key-here`",
                icon="⚠️",
            )
        base_url = None

    # OpenRouter needs a custom base_url
    if backend == "openrouter":
        base_url = "https://openrouter.ai/api/v1"

    st.divider()

    # ── Generation settings ──────────────────
    temperature = st.slider(
        "Temperature",
        0.0, 1.0, 0.7, 0.05,
        help="Higher = more creative. 0.7 is a good default.",
    )
    max_tokens = st.slider(
        "Max tokens per agent",
        500, 4000, 3000, 100,
        help="3000 is a good default for cloud models. Drop to 1000–1500 for local 7B models or Groq free tier.",
    )

    industry = st.selectbox(
        "Industry context",
        ["Generic", "Aerospace & Defence", "Automotive & Embedded",
         "Software Engineering", "Finance & Banking", "Healthcare & MedTech"],
        index=0,
        help="Writers emphasise sector-relevant qualities (standards, metrics, compliance). "
             "Generic works for any industry.",
    )

    llm_config = {
        "backend":     backend,
        "model":       model,
        "temperature": temperature,
        "max_tokens":  max_tokens,
        "api_key":     api_key,
        "base_url":    base_url,
        "industry":    industry,
    }

    st.divider()

    with st.expander("Word Document Settings", expanded=False):
        st.caption("Optional — fills the letter header in the downloaded .docx file.")
        st.session_state.candidate_phone = st.text_input(
            "Your phone",
            value=st.session_state.candidate_phone,
            placeholder="e.g. +49 151 12345678",
            key="docx_phone_input",
        )
        st.session_state.candidate_address = st.text_input(
            "Your address",
            value=st.session_state.candidate_address,
            placeholder="e.g. Musterstr. 1, 12345 Berlin",
            key="docx_address_input",
        )
        st.session_state.candidate_email = st.text_input(
            "Your email",
            value=st.session_state.candidate_email,
            placeholder="e.g. your@email.com",
            key="docx_email_input",
        )

    st.divider()
    st.caption("Powered by CrewAI + LiteLLM")
    st.caption("7 agents · 8 tasks · 2 English cover letter variants")


# ─────────────────────────────────────────────
#  MAIN AREA
# ─────────────────────────────────────────────

st.title("Cover Letter Crew")
st.caption("8 steps · JD Analysis → Resume Analysis → Skill Gap → ATS Keywords → EN Formal → EN Modern → Fact Checker → Reviewer")

# Load profile.py into session state on first run
_load_default_profile()

# ── PROFILE SECTION ──────────────────────────

with st.expander("Candidate Profile", expanded=not bool(st.session_state.profile_text)):

    st.markdown("**Upload resume files** (PDF, DOCX, or TXT):")
    uploaded_files = st.file_uploader(
        "Upload CV",
        type=["pdf", "docx", "txt"],
        accept_multiple_files=True,
        label_visibility="collapsed",
        key="file_uploader",
    )

    if uploaded_files:
        new_names = [f.name for f in uploaded_files]
        if new_names != st.session_state.files_processed:
            with st.spinner("Extracting text from files..."):
                from file_parser import extract_texts_from_uploads
                extracted = extract_texts_from_uploads(uploaded_files)
            if extracted.strip():
                st.session_state.profile_text    = extracted
                st.session_state.profile_editor  = extracted  # sync widget key so text_area reflects the upload
                st.session_state.files_processed = new_names
                # Try to pre-detect name from text + first filename on upload
                _fname = uploaded_files[0].name if uploaded_files else ""
                _detected = _extract_candidate_name(extracted, _fname)
                if _detected and "candidate_name_input" not in st.session_state:
                    st.session_state["candidate_name_input"] = _detected
                st.success(f"Extracted text from {len(uploaded_files)} file(s). You can edit it below.")
            else:
                st.warning("Could not extract text from the uploaded files.")

    st.markdown("**Or paste / edit profile text directly:**")
    edited_profile = st.text_area(
        "Profile text",
        value=st.session_state.profile_text,
        height=280,
        label_visibility="collapsed",
        placeholder="Paste your CV / resume text here, or upload files above...",
        key="profile_editor",
    )
    st.session_state.profile_text = edited_profile

    # Invalidate profile cache if text has changed since last parse
    if edited_profile != st.session_state.profile_cache_text:
        st.session_state.profile_parsed = None

    # ── Candidate name detection ──────────────
    # Apply any pending programmatic update (from AI detect) before widget instantiates
    if "candidate_name_pending" in st.session_state:
        st.session_state["candidate_name_input"] = st.session_state.pop("candidate_name_pending")

    # Regex pre-populate: only on first render (before widget key exists in state)
    if "candidate_name_input" not in st.session_state and edited_profile.strip():
        _fname0 = st.session_state.files_processed[0] if st.session_state.files_processed else ""
        _regex_name = _extract_candidate_name(edited_profile, _fname0)
        if _regex_name:
            st.session_state["candidate_name_input"] = _regex_name

    cname_col, cname_btn_col = st.columns([4, 1])
    with cname_col:
        cname = st.text_input(
            "Your full name (used in letter sign-offs)",
            key="candidate_name_input",
            placeholder="e.g. Jane Smith",
        )
        st.session_state.candidate_name = cname
    with cname_btn_col:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔍 Detect", help="Use AI to detect name from profile text", key="detect_name_btn"):
            if edited_profile.strip():
                with st.spinner("Detecting name…"):
                    ai_name = _llm_extract_name(edited_profile, llm_config)
                if ai_name:
                    st.session_state["candidate_name_pending"] = ai_name
                    st.rerun()
                else:
                    st.warning("Could not detect name — please enter it manually.")
            else:
                st.warning("Upload or paste your profile first.")

    if not cname.strip():
        st.warning("Name not detected — please enter your name so letters are signed correctly.", icon="⚠️")
    else:
        st.caption("✅ Name ready")

    col_save, col_clear, _ = st.columns([1, 1, 5])
    with col_save:
        if st.button("Save as default", help="Writes profile text to profile.py so it pre-loads next time"):
            if edited_profile.strip():
                _save_profile_to_file(edited_profile.strip(), st.session_state.candidate_name)
                st.success("Saved to profile.py")
            else:
                st.warning("Nothing to save — profile is empty.")
    with col_clear:
        if st.button("Clear", help="Clear the profile text area"):
            st.session_state.profile_text     = ""
            st.session_state.files_processed  = []
            st.session_state.profile_parsed   = None
            st.session_state.profile_cache_text = ""
            st.rerun()

# ── JOB POSTING SECTION ──────────────────────

st.markdown("### Job Posting")
st.caption("Paste the full job page — LinkedIn, any job board, plain text. The AI extracts everything it needs.")
job_raw = st.text_area(
    "Job posting",
    height=220,
    placeholder="Copy and paste everything — job title, company, full description, requirements...",
    label_visibility="collapsed",
    key="job_input",
)

# ── EMPLOYER NAME ────────────────────────────
# Auto-detect via regex, then allow AI fallback, then manual entry.

_prev_company  = (st.session_state.job_meta or {}).get("company", "")
_regex_company = _extract_company_regex(job_raw) if job_raw.strip() else ""
_anon_values   = ("", "your organisation", "unknown", "unser kunde", "our client")
_auto_company  = (_prev_company if _prev_company.lower() not in _anon_values else "") or _regex_company

# Apply any pending programmatic update (from AI detect) before widget instantiates
if "employer_name_pending" in st.session_state:
    st.session_state["employer_name_widget"] = st.session_state.pop("employer_name_pending")

# Regex/previous-run pre-populate: only on first render (before widget key exists)
if "employer_name_widget" not in st.session_state and _auto_company:
    st.session_state["employer_name_widget"] = _auto_company

emp_col, emp_btn_col = st.columns([4, 1])
with emp_col:
    employer_input = st.text_input(
        "Employer / Company name",
        key="employer_name_widget",
        placeholder="e.g. Airbus, Diehl Aerospace — AI will detect if left blank",
    )
    st.session_state.employer_name = employer_input
with emp_btn_col:
    st.markdown("<br>", unsafe_allow_html=True)
    if st.button("🔍 Detect", help="Use AI to detect employer name from job posting", key="detect_company_btn"):
        if job_raw.strip():
            with st.spinner("Detecting employer…"):
                ai_company = _llm_extract_company(job_raw, llm_config)
            if ai_company:
                st.session_state["employer_name_pending"] = ai_company
                st.rerun()
            else:
                st.warning("Could not detect employer — please enter it manually.")
        else:
            st.warning("Paste a job posting first.")

if not employer_input.strip() and job_raw.strip():
    st.caption("ℹ️ Employer not detected — enter the company name or leave blank (AI will attempt to extract it).")
elif _regex_company and employer_input.strip() == _regex_company:
    st.caption("✅ Employer detected from job posting")

company_override = employer_input.strip() or None

# ── LANGUAGE + GENERATE ──────────────────────

st.markdown("---")
_lang_col, _gen_col = st.columns([1, 2])
with _lang_col:
    lang_pref = st.selectbox(
        "Output language",
        _LANGUAGES,
        index=0,
        key="lang_pref_select",
        help=(
            "English: generate only in English.\n"
            "Any other: generate in English, then a one-click translate step "
            "appears in the results so you can get the translated version too."
        ),
    )
with _gen_col:
    st.markdown("<br>", unsafe_allow_html=True)
    generate_clicked = st.button(
        "Generate Cover Letters",
        type="primary",
        disabled=st.session_state.is_running,
        use_container_width=True,
    )

if generate_clicked:
    if not st.session_state.profile_text.strip():
        st.error("Please provide your candidate profile first — upload a file or paste text in the section above.")
        st.stop()
    if not job_raw.strip():
        st.error("Please paste a job description before generating.")
        st.stop()

    st.session_state.results   = None
    st.session_state.run_error = None
    st.session_state.is_running = True
    _run_crew(
        profile_text=st.session_state.profile_text,
        job_raw=job_raw,
        llm_config=llm_config,
        company_override=company_override,
        candidate_name=st.session_state.candidate_name,
        candidate_address=st.session_state.candidate_address,
        candidate_email=st.session_state.candidate_email,
        candidate_phone=st.session_state.candidate_phone,
        structured_profile=st.session_state.profile_parsed,
        industry=llm_config.get("industry", "Generic"),
    )

# ── ERROR DISPLAY ─────────────────────────────

if st.session_state.run_error:
    st.error(f"Generation failed: {st.session_state.run_error}")
    with st.expander("Troubleshooting tips"):
        st.markdown("""
- **Ollama not running**: Start Ollama (`ollama serve`) and ensure the model is pulled (`ollama pull qwen2.5:7b`)
- **Model missing**: Run `ollama pull <model-name>` in your terminal
- **DeepSeek API error**: Check your API key is correct in the sidebar or `.env` file
- **Out of memory**: Try a smaller model (e.g. `llama3.2:3b`) or reduce Max tokens in the sidebar
- **Timeout**: The model may be too slow; try DeepSeek API for faster results
        """)

# ── RESULTS ──────────────────────────────────

if st.session_state.results:
    results  = st.session_state.results
    job_meta = st.session_state.job_meta

    st.success(
        f"Generated 2 English cover letters for **{job_meta['job_title']}** at **{job_meta['company']}**"
    )

    # ── MATCH SCORE CIRCLE ───────────────────
    _match = results.get("match_score")
    if _match is not None:
        _l, _m, _r = st.columns([2, 1, 2])
        with _m:
            _render_match_circle(_match)

    # Parsed profile summary (cached from this run or previous)
    _parsed = st.session_state.profile_parsed
    if _parsed and "companies" in _parsed:
        with st.expander("Parsed Profile Summary", expanded=False):
            st.caption("Structured data extracted from your CV — used by all agents.")
            pc1, pc2, pc3 = st.columns(3)
            pc1.metric("Name",       _parsed.get("name") or "—")
            pc2.metric("Current role", _parsed.get("current_role") or "—")
            pc3.metric("Experience", f"{_parsed.get('total_years') or '?'} yrs")
            if _parsed.get("companies"):
                st.markdown("**Experience:**")
                for c in _parsed["companies"]:
                    st.markdown(f"- **{c.get('name','')}** ({c.get('start','?')}–{c.get('end','?')}) — {c.get('role','')}")
            col_skills, col_tools = st.columns(2)
            if _parsed.get("skills_technical"):
                col_skills.markdown(f"**Skills:** {', '.join(_parsed['skills_technical'])}")
            if _parsed.get("skills_tools"):
                col_tools.markdown(f"**Tools:** {', '.join(_parsed['skills_tools'])}")
            if st.button("🔄 Re-parse Profile", help="Force re-parse of the candidate profile"):
                st.session_state.profile_parsed    = None
                st.session_state.profile_cache_text = ""
                st.rerun()

    # Parsed job metadata
    with st.expander("Parsed job info", expanded=False):
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Company",  job_meta["company"])
        c2.metric("Position", job_meta["job_title"])
        c3.metric("Location", job_meta["location"] or "—")
        c4.metric("Ref",      job_meta["ref_number"] or "—")

    # ── ENGLISH LETTERS ──────────────────────
    tab1, tab2 = st.tabs(["EN Formal", "EN Modern"])
    for tab, text in [(tab1, results["en_formal"]), (tab2, results["en_modern"])]:
        with tab:
            if text and text.strip():
                st.code(text.strip(), language=None, wrap_lines=True)
                st.caption("Click inside the block, press Ctrl+A then Ctrl+C to copy all.")
            else:
                st.warning("This variant was not generated successfully. Try re-running.")

    # ── TRANSLATION ──────────────────────────
    _default_lang_idx = max(0, _LANGUAGES.index(lang_pref) - 1) if lang_pref != "English" else 0
    _non_en = [l for l in _LANGUAGES if l != "English"]

    with st.expander(
        "Translate to another language",
        expanded=(lang_pref != "English"),
    ):
        _tcol1, _tcol2 = st.columns([2, 1])
        with _tcol1:
            trans_lang_sel = st.selectbox(
                "Translate to",
                _non_en,
                index=_default_lang_idx,
                key="trans_lang_select",
            )
        with _tcol2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Translate", key="translate_btn", use_container_width=True):
                from cover_letter_crew import translate_letter
                with st.spinner(f"Translating to {trans_lang_sel}…"):
                    st.session_state.trans_formal = translate_letter(
                        results["en_formal"], trans_lang_sel, llm_config)
                    st.session_state.trans_modern = translate_letter(
                        results["en_modern"], trans_lang_sel, llm_config)
                    st.session_state.trans_lang = trans_lang_sel
                st.rerun()

        if st.session_state.trans_formal:
            _tl = st.session_state.trans_lang
            st.success(f"Translated to {_tl}")
            tt1, tt2 = st.tabs([f"{_tl} - Formal", f"{_tl} - Modern"])
            with tt1:
                st.code(st.session_state.trans_formal, language=None, wrap_lines=True)
                st.caption("Click inside the block, press Ctrl+A then Ctrl+C to copy all.")
            with tt2:
                st.code(st.session_state.trans_modern, language=None, wrap_lines=True)
                st.caption("Click inside the block, press Ctrl+A then Ctrl+C to copy all.")

    # ── DOWNLOAD ─────────────────────────────
    st.markdown("#### Download")
    st.caption("Select which variants to include in the Word document:")

    _dl_cols = st.columns(4)
    with _dl_cols[0]:
        dl_en_f = st.checkbox("EN Formal",  value=True,  key="dl_en_formal")
    with _dl_cols[1]:
        dl_en_m = st.checkbox("EN Modern",  value=True,  key="dl_en_modern")
    if st.session_state.trans_formal:
        _tl = st.session_state.trans_lang
        with _dl_cols[2]:
            dl_tr_f = st.checkbox(f"{_tl} Formal", value=True,  key="dl_tr_formal")
        with _dl_cols[3]:
            dl_tr_m = st.checkbox(f"{_tl} Modern", value=False, key="dl_tr_modern")
    else:
        dl_tr_f = dl_tr_m = False

    _sel_variants = []
    if dl_en_f: _sel_variants.append(("English - Formal", results["en_formal"]))
    if dl_en_m: _sel_variants.append(("English - Modern", results["en_modern"]))
    if dl_tr_f: _sel_variants.append((f"{st.session_state.trans_lang} - Formal", st.session_state.trans_formal))
    if dl_tr_m: _sel_variants.append((f"{st.session_state.trans_lang} - Modern", st.session_state.trans_modern))

    if _sel_variants:
        from cover_letter_crew import build_docx_bytes
        import re as _re
        _sfn = lambda s: (''.join(w.capitalize() for w in _re.sub(r'[^\w\s]', '', s.strip()).split())[:28] or 'Doc')
        _fn = (
            f"CoverLetter"
            f"_{_sfn(st.session_state.candidate_name or 'Candidate')}"
            f"_{_sfn(job_meta.get('job_title','Position'))}"
            f"_{_sfn(job_meta.get('company','Company'))}"
            f"_{__import__('datetime').date.today().strftime('%d%m%Y')}.docx"
        )
        _dl_bytes = build_docx_bytes(
            job_meta, _sel_variants,
            candidate_name=st.session_state.candidate_name,
            candidate_address=st.session_state.candidate_address,
            candidate_email=st.session_state.candidate_email,
            candidate_phone=st.session_state.candidate_phone,
        )
        _variant_label = " + ".join(l for l, _ in _sel_variants)
        st.download_button(
            label=f"Download .docx  —  {_variant_label}",
            data=_dl_bytes,
            file_name=_fn,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            use_container_width=True,
        )
    else:
        st.caption("Select at least one variant above to enable download.")
