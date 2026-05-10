"""
============================================================
  AFEEF'S COVER LETTER CREW
  CrewAI-powered — generates 2 English cover letter variants per job

  CLI usage:
    python cover_letter_crew.py

  Streamlit usage:
    streamlit run app.py

  Requirements:
    - DEEPSEEK_API_KEY set in .env  (cheapest option)
    - OR Ollama running locally with qwen2.5:7b (free)
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
from crewai import Agent, Task, Crew, LLM
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

load_dotenv()

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

OLLAMA_BASE_URL = "http://localhost:11434"
OLLAMA_MODEL    = "qwen2.5:7b"


def _extract_name_from_profile(text: str) -> str:
    """Multi-strategy regex name extraction — no LLM cost."""
    m = re.search(r'(?i)(?:full\s+)?name\s*[:\-]\s*([A-Z][a-zA-ZÀ-ÿ]+(?:\s[A-Z][a-zA-ZÀ-ÿ]+){1,3})', text)
    if m:
        return m.group(1).strip()
    for line in text.strip().splitlines()[:15]:
        line = line.strip()
        if 4 < len(line) < 50 and re.match(r'^[A-Z][a-zA-ZÀ-ÿ]+(\s[A-Z][a-zA-ZÀ-ÿ]+){1,3}$', line):
            return line
    return ""


def _ollama_is_running() -> bool:
    try:
        urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def _start_ollama(fatal: bool = True):
    """Start Ollama in the background and wait until it responds.
    If fatal=False, raises RuntimeError instead of sys.exit."""
    print("Ollama not running — starting it now...")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
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
        if _ollama_is_running():
            print(" ready!")
            return
    msg = "Ollama did not start in time. Check your installation."
    print(f"\nERROR: {msg}")
    if fatal:
        sys.exit(1)
    raise RuntimeError(msg)


def _model_is_pulled(model: str) -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_BASE_URL}/api/tags", timeout=5) as resp:
            data = _json.loads(resp.read())
        local_names = [m["name"] for m in data.get("models", [])]
        return any(name == model or name.startswith(model.split(":")[0] + ":") for name in local_names)
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


def _ensure_ollama_or_raise(model: str):
    """GUI-mode: auto-start Ollama and pull model; raises RuntimeError on failure."""
    if not _ollama_is_running():
        _start_ollama(fatal=False)
    if not _model_is_pulled(model):
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

def get_llm(llm_config: dict | None = None) -> LLM:
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

    If llm_config is None, uses env-based auto-detect (CLI backwards compat):
        DEEPSEEK_API_KEY → DeepSeek
        else             → Ollama qwen2.5:7b
    """
    if llm_config is None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if api_key:
            print("Using DeepSeek API")
            return LLM(model="deepseek/deepseek-chat", api_key=api_key,
                       temperature=0.7, max_tokens=3000)
        print("No API key — using local Ollama (qwen2.5:7b)")
        _ensure_ollama()
        return LLM(model=f"ollama/{OLLAMA_MODEL}", base_url=OLLAMA_BASE_URL,
                   temperature=0.7, max_tokens=3000)

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
        _ensure_ollama_or_raise(model)
        return LLM(model=f"ollama/{model}", base_url=_base,
                   temperature=temperature, max_tokens=max_tokens)

    elif backend == "deepseek":
        key = _require_key("DEEPSEEK_API_KEY")
        return LLM(model=f"deepseek/{model}", api_key=key,
                   temperature=temperature, max_tokens=max_tokens)

    elif backend == "anthropic":
        key = _require_key("ANTHROPIC_API_KEY")
        return LLM(model=f"anthropic/{model}", api_key=key,
                   temperature=temperature, max_tokens=max_tokens)

    elif backend == "openai":
        key = _require_key("OPENAI_API_KEY")
        return LLM(model=model, api_key=key,
                   temperature=temperature, max_tokens=max_tokens)

    elif backend == "gemini":
        key = _require_key("GOOGLE_API_KEY")
        # Use Google's OpenAI-compatible endpoint instead of the native google-genai
        # provider — the native provider is locked to v1beta and has very limited
        # model availability. The OpenAI-compatible endpoint supports the full range.
        return LLM(model=f"openai/{model}", api_key=key,
                   base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                   temperature=temperature, max_tokens=max_tokens)

    elif backend == "groq":
        key = _require_key("GROQ_API_KEY")
        return LLM(model=f"groq/{model}", api_key=key,
                   temperature=temperature, max_tokens=max_tokens)

    elif backend == "openrouter":
        key = _require_key("OPENROUTER_API_KEY")
        _base = base_url or "https://openrouter.ai/api/v1"
        return LLM(model=f"openrouter/{model}", api_key=key, base_url=_base,
                   temperature=temperature, max_tokens=max_tokens)

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
    print("  AFEEF'S COVER LETTER CREW")
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


def clean_job_paste(raw: str, llm: LLM, interactive: bool = True) -> dict:
    """
    Use an LLM agent to strip UI noise and extract structured job data.
    Set interactive=False for Streamlit (skips input() confirmation).
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
""",
        agent=cleaner,
        expected_output='JSON object with keys: company_name, job_title, location, ref_number, job_description'
    )

    mini_crew = Crew(agents=[cleaner], tasks=[parse_task], verbose=False)
    result    = mini_crew.kickoff()
    raw_out   = str(result).strip()

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


def parse_profile(profile_text: str, llm: LLM) -> dict:
    """One LLM call → structured profile dict. Fallback: {"raw": profile_text}."""
    import json as _json_mod
    try:
        raw = llm.call([{"role": "user", "content": _PARSE_PROFILE_PROMPT + profile_text[:4000]}])
        m = re.search(r'\{[\s\S]+\}', raw)
        if m:
            return _json_mod.loads(m.group(0))
    except Exception:
        pass
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


def build_crew(job: dict, llm: LLM, profile_text: str | None = None, step_callback=None,
               candidate_name: str = "the candidate",
               structured_profile: dict | None = None,
               industry: str = "Generic") -> Crew:
    """
    Build and return a 7-agent, 8-task CrewAI Crew (English-only pipeline).
    profile_text  : raw CV text (fallback if structured_profile not supplied)
    structured_profile : pre-parsed profile dict from parse_profile()
    candidate_name: used in sign-offs and writer context headers
    industry      : sector hint injected into each writer task
    """
    _profile = profile_text if profile_text is not None else CANDIDATE_PROFILE
    _profile_block = _format_structured_profile(structured_profile) if structured_profile else _profile
    _ref     = job['ref_number'] if job['ref_number'] else 'N/A'
    _industry_ctx = _INDUSTRY_CONTEXT.get(industry, "")

    # ══════════════════════════════════════════
    #  AGENTS
    # ══════════════════════════════════════════

    job_analyst = Agent(
        role="Job Analyst",
        goal="Extract the key technical requirements, must-haves, and culture signals from a job description.",
        backstory="Expert technical recruiter with 15 years in embedded systems and aerospace hiring. Reads JDs instantly and spots what really matters.",
        llm=llm, verbose=False
    )

    resume_analyzer = Agent(
        role="Resume Analyzer",
        goal="Deeply analyse the candidate's CV and produce a structured skills and experience breakdown.",
        backstory="Senior career consultant specialising in STEM profiles. Extracts concrete skills, domain expertise, and standout achievements from CVs.",
        llm=llm, verbose=False
    )

    skill_gap_mapper = Agent(
        role="Skill Gap Mapper",
        goal="Compare the job requirements against the candidate's profile and identify strong matches, partial matches, and gaps.",
        backstory="Talent analyst who maps candidate profiles to job requirements with precision. Knows how to frame gaps honestly without hurting the application.",
        llm=llm, verbose=False
    )

    ats_optimizer = Agent(
        role="ATS Keyword Optimizer",
        goal="Identify the exact ATS keywords from the job description and create keyword-enriched talking points the writers must use.",
        backstory="ATS systems expert who knows that 75% of CVs are rejected by algorithms before a human reads them. Ensures the right keywords appear naturally in the letter.",
        llm=llm, verbose=False
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
        llm=llm, verbose=False
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

POSITION: {job['job_title']} at {job['company']}
LOCATION: {job['location']}

JOB DESCRIPTION:
{job['job_desc']}

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
""",
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
""",
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
were involved, and how it connects to THIS job. Do not invent.]
""",
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
""",
        agent=ats_optimizer,
        expected_output="ATS optimization: MUST_USE_KEYWORDS, KEYWORD_TALKING_POINTS, ATS_OPENING",
        context=[jd_task, gap_task]
    )

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

STRICT RULES:
- LANGUAGE: Do NOT write any German word in this letter. If a term from the JD is in German,
  translate it using MUST_USE_KEYWORDS_EN — never embed the German form.
  These technical proper nouns are exempt (keep exactly as-is): DO-178C, AUTOSAR, VxWorks,
  ASIL B, MISRA C, MIL/SIL/HIL, CANoe, WinIdea, Simulink, DOORS, FOC, IPMSM, MXAM,
  Polyspace, TESSY, A-SPICE. All other words must be English.
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
Output ONLY the letter body. No subject line, no date, no commentary, no markdown.
""",
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

STRICT RULES:
- LANGUAGE: Do NOT write any German word in this letter. If a term from the JD is in German,
  translate it using MUST_USE_KEYWORDS_EN — never embed the German form.
  These technical proper nouns are exempt (keep exactly as-is): DO-178C, AUTOSAR, VxWorks,
  ASIL B, MISRA C, MIL/SIL/HIL, CANoe, WinIdea, Simulink, DOORS, FOC, IPMSM, MXAM,
  Polyspace, TESSY, A-SPICE. All other words must be English.
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
Output ONLY the letter body. No subject line, no date, no commentary, no markdown.
""",
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
""",
        agent=fact_checker,
        expected_output="Structured violation report: EN_FORMAL_VIOLATIONS and EN_MODERN_VIOLATIONS with HARD/SOFT/CALIBRATION/PASS sections, plus SUMMARY counts",
        context=[en_formal_task, en_modern_task, gap_task, resume_task]
    )

    # Task 7 — Tone + Grammar Review (final output)
    review_task = Task(
        description=f"""
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
""",
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
        verbose=True,
        step_callback=step_callback
    )

    return crew


# ─────────────────────────────────────────────
#  SAVE TO WORD (.docx)
# ─────────────────────────────────────────────

def _safe_part(s: str, max_len: int = 28) -> str:
    import re as _re
    words = _re.sub(r'[^\w\s]', '', s.strip()).split()
    return (''.join(w.capitalize() for w in words)[:max_len] or 'Unknown')


def _build_docx_doc(job: dict, variants: list,
                    candidate_name: str = "", candidate_address: str = "",
                    candidate_email: str = "", candidate_phone: str = "") -> "Document":
    """
    Core builder. `variants` is a list of (label, letter_text) tuples,
    e.g. [("English - Formal", "..."), ("German - Formal", "...")].
    """
    doc = Document()

    for section in doc.sections:
        section.top_margin    = Inches(1.0)
        section.bottom_margin = Inches(1.0)
        section.left_margin   = Inches(1.2)
        section.right_margin  = Inches(1.2)

    style = doc.styles['Normal']
    style.font.name = 'Arial'
    style.font.size = Pt(11)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run("COVER LETTER PACKAGE")
    run.bold = True
    run.font.size = Pt(16)
    run.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)

    doc.add_paragraph()

    info_lines = [
        f"Candidate : {candidate_name or 'N/A'}",
        f"Position  : {job['job_title']}",
        f"Company   : {job['company']}",
        f"Location  : {job['location']}",
        f"Generated : {datetime.now().strftime('%d %B %Y, %H:%M')}",
    ]
    if job.get('ref_number'):
        info_lines.append(f"Reference : {job['ref_number']}")

    for line in info_lines:
        p = doc.add_paragraph(line)
        p.runs[0].font.size = Pt(10)

    doc.add_paragraph()

    variant_word = "variant" if len(variants) == 1 else "variants"
    note = doc.add_paragraph(
        f"This package contains {len(variants)} cover letter {variant_word}. "
        "Choose the one that best fits the company culture and language requirement."
    )
    note.runs[0].italic = True
    note.runs[0].font.size = Pt(10)

    today = datetime.now().strftime("%d %B %Y")

    for i, (label, letter_text) in enumerate(variants):
        doc.add_page_break()

        header = doc.add_paragraph()
        header.alignment = WD_ALIGN_PARAGRAPH.LEFT
        h_run = header.add_run(f"VARIANT {i+1}   {label}")
        h_run.bold = True
        h_run.font.size = Pt(13)
        h_run.font.color.rgb = RGBColor(0x1F, 0x49, 0x7D)

        div = doc.add_paragraph()
        div.paragraph_format.space_after = Pt(12)

        header_block = []
        if candidate_name:
            header_block.append(candidate_name)
        if candidate_phone:
            header_block.append(candidate_phone)
        if candidate_address:
            header_block.append(candidate_address)
        if candidate_email:
            header_block.append(candidate_email)
        header_block += ["", today, "", job['company'], job.get('location', '')]
        if job.get('ref_number'):
            header_block.append(f"Re: {job['job_title']} — Ref: {job['ref_number']}")
        else:
            header_block.append(f"Re: {job['job_title']}")

        for line in header_block:
            p = doc.add_paragraph(line if line else " ")
            p.runs[0].font.size = Pt(10) if line else Pt(6)
            p.paragraph_format.space_after = Pt(0)

        doc.add_paragraph()

        body = str(letter_text).strip() if letter_text else "[Letter generation failed]"
        for para in body.split("\n"):
            para = para.strip()
            if para:
                p = doc.add_paragraph(para)
                p.paragraph_format.space_after = Pt(8)
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

        doc.add_paragraph()

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
    "German":     "Use formal 'Sie' throughout. Sign off with 'Mit freundlichen Grüßen'.",
    "French":     "Use formal 'vous' (vouvoiement). Sign off with 'Veuillez agréer mes sincères salutations'.",
    "Spanish":    "Use formal 'usted'. Sign off with 'Atentamente'.",
    "Italian":    "Use formal 'Lei'. Sign off with 'Distinti saluti'.",
    "Dutch":      "Use formal 'u'. Sign off with 'Met vriendelijke groet'.",
    "Portuguese": "Use formal 'você'. Sign off with 'Atenciosamente'.",
    "Polish":     "Use formal address. Sign off with 'Z poważaniem'.",
    "Swedish":    "Use formal address. Sign off with 'Med vänliga hälsningar'.",
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
        f"Translate this professional cover letter into {target_lang}.\n\n"
        f"Rules:\n"
        f"- {register}\n"
        f"- Preserve ALL of the following exactly as written (do NOT translate): "
        f"company names, candidate name, job titles, "
        f"technical terms and standards ({_PRESERVED_TERMS}).\n"
        f"- Keep the same paragraph structure and approximate length.\n"
        f"- Do NOT add, remove, or paraphrase content — only translate.\n"
        f"- Output ONLY the translated letter text, nothing else.\n\n"
        f"LETTER:\n{text}"
    )
    result = llm.call([{"role": "user", "content": prompt}])
    return result.strip() if isinstance(result, str) else str(result).strip()


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
        m = _re.search(
            rf'(?m)^{header}:\s*\n(.*?)(?=\n[A-Z_]{{3,}}:|$)',
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
    llm = get_llm(llm_config)
    if pre_parsed_job is not None:
        job = {**pre_parsed_job}
        if not job.get("job_desc"):
            job["job_desc"] = job_raw
    else:
        job = clean_job_paste(job_raw, llm, interactive=False)

    if company_override:
        job["company"] = company_override

    # Parse profile if not already cached
    if structured_profile is None:
        structured_profile = parse_profile(profile_text, llm)

    _cname = candidate_name.strip() or (structured_profile.get("name") if "name" in structured_profile else "") or _extract_name_from_profile(profile_text)
    crew = build_crew(job, llm, profile_text=profile_text, step_callback=step_callback,
                      candidate_name=_cname or "the candidate",
                      structured_profile=structured_profile,
                      industry=industry)
    tasks_output = crew.kickoff()
    outputs = tasks_output.tasks_output

    # Task index map: 0=jd, 1=resume, 2=gap, 3=ats, 4=en_formal, 5=en_modern,
    #                 6=fact_check, 7=review (final)
    #
    # Primary: parse the reviewer's combined output (task 7)
    # Fallback: use the raw writer outputs (tasks 4-5) if reviewer parsing fails
    reviewed = {}
    if len(outputs) > 7 and outputs[7].raw:
        reviewed = _parse_reviewer_output(outputs[7].raw)

    def _pick(reviewed_text: str, writer_raw: str) -> str:
        return reviewed_text.strip() if reviewed_text.strip() else writer_raw

    letters = [
        _pick(reviewed.get("en_formal", ""), outputs[4].raw if len(outputs) > 4 else ""),
        _pick(reviewed.get("en_modern", ""), outputs[5].raw if len(outputs) > 5 else ""),
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
    def _pick(r, w):
        return r.strip() if r.strip() else w
    letter_results = [
        _pick(reviewed.get("en_formal", ""), task_outputs[4].raw if len(task_outputs) > 4 else ""),
        _pick(reviewed.get("en_modern", ""), task_outputs[5].raw if len(task_outputs) > 5 else ""),
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
