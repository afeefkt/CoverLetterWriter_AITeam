# Cover Letter Crew

*A sample project exploring agentic AI with CrewAI.*

---

This project started as a **vibe-coding experiment** — my way of learning how agentic AI actually works in practice, not just in theory.

The idea was simple: can I build something genuinely useful using multiple AI agents collaborating on a real task, without it turning into a hallucination machine? Turns out — yes, if you think carefully about how agents hand off context to each other.

Two scenarios drove the design:

- 🔒 **Local AI (privacy-first)** — runs entirely on your laptop using [Ollama](https://ollama.com). No data leaves your machine. Completely free. Good enough for most use cases.
- ☁️ **Cloud APIs (maximum quality)** — swap to DeepSeek, Claude, GPT-4o, Gemini, or Groq at runtime for noticeably better output. Optional.

Built with **[CrewAI](https://crewai.com)** — a Python framework for orchestrating multi-agent AI pipelines. Each agent has one narrow job; they pass structured context to each other rather than trying to do everything in one giant prompt.

> **This is a learning project.** It works well enough to produce real, usable cover letters — but expect rough edges, and always review the output before sending anything. Feedback and pull requests welcome.

What it does in practice: paste a job posting + upload your CV → 7 AI agents collaborate across 8 tasks → 2 polished, fact-checked cover letters in ~2 minutes, with an optional German (or multi-language) translation step.

---

## Quick Start

### 1. Clone and set up

**Windows:**
```bat
git clone <repo-url>
cd CoverLetterWriter_AITeam
setup.bat
```

**Mac / Linux:**
```bash
git clone <repo-url>
cd CoverLetterWriter_AITeam
chmod +x setup.sh && ./setup.sh
```

`setup` creates a `.venv`, installs all dependencies, and copies `.env.example` → `.env`.

### 2. Add your API key (optional — skip for Ollama)

Open `.env` and fill in the key for your chosen provider. See `.env.example` for all options.

For the free local option, pull the default model instead:
```bash
ollama pull qwen3.5:9b
```

### 3. Launch

**Windows web UI:** double-click `Run_Streamlit.bat`  
**Mac/Linux web UI:** `bash Run.sh`  
**CLI:** `python cover_letter_crew.py`

Open `http://localhost:8501` in your browser.

---

## Features

| Feature | Details |
|---|---|
| Resume upload | Upload PDF, DOCX, or TXT files — text is extracted automatically |
| Raw job paste | Paste the full LinkedIn page — UI noise is stripped by an agent |
| 2 letter variants | EN Formal · EN Modern |
| Fact Checker | Dedicated agent detects hallucinations before the reviewer applies fixes |
| Match score | Circular gauge showing job ↔ profile fit % before you commit to applying |
| Copy-ready output | Each letter in a code block — select all, Ctrl+C, done |
| Translation | One-click AI translation to German, French, Spanish, Italian + more (formal register aware) |
| Word export | Checkbox-select which variants to include; filename: `CoverLetter_Name_Title_Company_Date.docx` |
| LLM flexibility | Ollama (local/free) or any cloud API (7 providers); switch at runtime, no code changes |
| CLI mode | `python cover_letter_crew.py` still works unchanged |

---

## Multi-Agent Architecture & Workflow

The system uses **7 AI agents** across **8 tasks** in a sequential pipeline. The key architectural principle is **detection → correction separation**: a dedicated Fact Checker finds hallucinations first; the Reviewer applies the fixes rather than trying to detect and fix simultaneously.

```mermaid
flowchart TD
    %% ── INPUTS ──────────────────────────────────────────────────
    RAW(["📋 Raw Job Posting\n(paste from LinkedIn / job board)"])
    CV(["👤 Candidate Profile\n(upload PDF · DOCX · TXT\nor paste text)"])

    %% ── STAGE 0 ──────────────────────────────────────────────────
    subgraph S0["⚙️  Stage 0 — Pre-processing  (mini-crew, runs first)"]
        PARSER["🤖 Job Page Parser\n\nStrips: buttons, nav bars, share prompts,\npremium upsells, 'People you may know'\n\nExtracts structured JSON:\n• company_name\n• job_title\n• location\n• ref_number\n• job_description  ← clean text only"]
    end

    RAW --> PARSER
    PARSER --> JOB[/"Structured Job Data\ncompany · title · location · ref · JD text"/]

    %% ── STAGE 1 ──────────────────────────────────────────────────
    subgraph S1["🔍  Stage 1 — Job Analysis"]
        JDA["🤖 Job Analyst\n\nReads the clean JD text\n\nOutputs labelled sections:\nTECHNICAL_REQUIREMENTS  (top 5)\nSOFT_SKILLS  (top 3)\nKEYWORDS  (comma-separated)\nTOOLS_AND_STANDARDS"]
    end

    JOB --> JDA
    JDA --> ANA[/"JD Analysis\n(structured labels for reliable 7B output)"/]

    %% ── STAGE 2 ──────────────────────────────────────────────────
    subgraph S2["🎯  Stage 2 — Profile Matching + Truth Anchor"]
        MATCH["🤖 Profile Matcher\n\nReceives: JD Analysis + Candidate Profile\n\nOutputs labelled sections:\nSTRONG_MATCHES  (4 specific CV ↔ JD links)\nPARTIAL_MATCHES  (adjacent skills + safe framing)\nPROHIBITED_CLAIMS  (must NOT appear in letters)\nSAFE_FRAMING  (exact bridge sentences to use)\nOPENING_HOOK · UNIQUE_ANGLE · AEROSPACE_FLAG"]
    end

    ANA --> MATCH
    CV  --> MATCH
    MATCH --> MA[/"Match Analysis + Truth Boundaries\n(shared context for writers and fact checker)"/]

    %% ── STAGE 3 ──────────────────────────────────────────────────
    subgraph S3["✍️  Stage 3 — Writing (2 agents, both read Match Analysis)"]
        direction LR
        W1["🤖 Formal English Writer\n\nStructured · traditional\n4 paragraphs · flowing prose\nConnector words allowed\n\nSign-off:\nMit freundlichen Grüßen /\nKind regards, [Name]"]
        W2["🤖 Modern English Writer\n\nPunchy · direct\nOpens with strongest hook\nNo filler phrases · em-dashes\n\nSign-off:\nBest regards, [Name]"]
    end

    MA --> W1 & W2

    %% ── STAGE 4 ──────────────────────────────────────────────────
    subgraph S4["🔎  Stage 4 — Fact Checking (NEW)"]
        FC["🤖 Fact Checker\n\nReceives: both letter drafts + Resume Analysis\n+ PROHIBITED_CLAIMS + SAFE_FRAMING\n\nGoes sentence-by-sentence — for every claim:\n  HARD violation → in PROHIBITED_CLAIMS, must remove\n  SOFT violation → adjacent overclaim, weaken\n  CALIBRATION   → language stronger than evidence\n  PASS           → verified against profile ✓\n\nOutputs structured VIOLATION REPORT only.\nDoes NOT rewrite letters."]
    end

    W1 & W2 --> FC
    FC --> VR[/"Violation Report\nEN_FORMAL_VIOLATIONS + EN_MODERN_VIOLATIONS\n+ SUMMARY counts"/]

    %% ── STAGE 5 ──────────────────────────────────────────────────
    subgraph S5["✅  Stage 5 — Review + Polish"]
        REV["🤖 Tone & Grammar Reviewer\n\nStep 1: Apply ALL violations from report (in order)\nStep 2: Fix salutation, language purity\nStep 3: Remove banned AI phrases\nStep 4: Grammar, technical terms\nStep 5: Paragraph depth (min 3 sentences each)\nStep 6: Sign-off completeness"]
    end

    VR --> REV
    W1 & W2 --> REV

    %% ── OUTPUTS ──────────────────────────────────────────────────
    REV --> L1["EN Formal"]
    REV --> L2["EN Modern"]

    subgraph OUT["📤  Output — Streamlit UI"]
        TABS["2 Tabs — one per variant\nEach letter in a copyable code block\n(click · Ctrl+A · Ctrl+C)"]
        DOCX["Optional: Download .docx\nBoth variants in a formatted Word file"]
    end

    L1 & L2 --> TABS
    TABS --> DOCX

    %% ── STYLES ───────────────────────────────────────────────────
    classDef agent  fill:#1F497D,color:#fff,stroke:#0d2d52
    classDef data   fill:#E8F0FB,color:#1a1a2e,stroke:#99b
    classDef output fill:#D6EAD6,color:#1a3a1a,stroke:#6a9a6a
    classDef input  fill:#FFF8E1,color:#333,stroke:#c9a

    class PARSER,JDA,MATCH,W1,W2,FC,REV agent
    class JOB,ANA,MA,VR,L1,L2 data
    class TABS,DOCX output
    class RAW,CV input
```

---

## How Context Flows Between Agents

```
                    [Raw Job Paste]
                          │
                    Job Page Parser
                          │
              [Structured Job Data: company, title, JD text]
                          │
                     Job Analyst
                          │
         [JD Analysis: requirements, skills, keywords]
                          │
                   Profile Matcher ◄─── [Candidate Profile]
                          │
          ┌───────────────┘
          │   PROHIBITED_CLAIMS + SAFE_FRAMING + STRONG_MATCHES
          ▼               ▼
    EN Formal Writer   EN Modern Writer
          │               │
          └───────┬───────┘
                  ▼
            Fact Checker  ◄── PROHIBITED_CLAIMS, SAFE_FRAMING, Resume Analysis
                  │
          [Violation Report: HARD / SOFT / CALIBRATION per letter]
                  │
              Reviewer ◄── both draft letters
                  │
          [Apply fixes → polish → output]
                  │
          ┌───────┴───────┐
    EN_FORMAL_FINAL    EN_MODERN_FINAL
```

The **Fact Checker** is the key architectural addition. It receives the two draft letters and the truth boundaries (PROHIBITED_CLAIMS, SAFE_FRAMING) produced by the Profile Matcher, then produces a structured violation report. The **Reviewer** receives this report and applies every fix before doing grammar and style work — detection and correction are fully separated.

---

## Stage-by-Stage Breakdown

### Stage 0 — Job Page Parser
A lightweight mini-crew that runs **before** the main pipeline. LinkedIn pages contain buttons, nav bars, premium upsells, "People you may know" sections, share/save prompts, etc. This agent strips all of that and returns a clean JSON object with only the actual job content.

### Stage 1 — Job Analyst
Reads the clean job description and produces a structured breakdown using **labelled sections** (`TECHNICAL_REQUIREMENTS:`, `SOFT_SKILLS:`, etc.). The explicit format is enforced so that even a 7B local model produces reliable, parseable output.

### Stage 2 — Profile Matcher + Truth Anchor
Receives both the JD Analysis and the full candidate profile. It produces:
- **STRONG_MATCHES** — specific one-to-one links between CV experience and JD requirements
- **PARTIAL_MATCHES** — adjacent skills with exact safe framing sentences to use
- **PROHIBITED_CLAIMS** — skills, tools, and job titles the JD requires that have NO direct evidence in the profile; writers must not claim these at all
- **SAFE_FRAMING** — for each prohibited or partial item, an exact bridge sentence the writers may use instead
- **OPENING_HOOK** and **UNIQUE_ANGLE** — for writers to use directly

The truth boundaries are enforced by the writers (TRUTH CONSTRAINT rule), verified by the Fact Checker, and applied by the Reviewer — three layers.

### Stage 3 — 2 Writing Agents
Each writer receives the Match Analysis and generates a complete letter body in their assigned style. They are constrained to use only SAFE_FRAMING for gap areas and must not claim anything under PROHIBITED_CLAIMS.

### Stage 4 — Fact Checker (NEW)
Goes sentence-by-sentence through both draft letters. For every claim about a skill, tool, domain, or job title, it checks:
- **Q1**: Is this directly evidenced in the candidate profile?
- **Q2**: If not — is it in PROHIBITED_CLAIMS? → **HARD violation**
- **Q3**: Is it an adjacent domain overclaim without using SAFE_FRAMING? → **SOFT violation**
- **Q4**: Does the language strength match the evidence depth? → **CALIBRATION issue**

Outputs a structured **VIOLATION REPORT** with the exact phrase, reason, and replacement for each issue. Does **not** rewrite letters.

### Stage 5 — Reviewer
Receives the two drafts and the violation report. Applies every HARD, SOFT, and CALIBRATION fix first (in order), then handles grammar, banned phrases, paragraph depth, and sign-off completeness.

---

## Why Separate Detection from Correction?

| Approach | Problem |
|---|---|
| TRUTH CONSTRAINT in writer prompt only | LLM generation instinct overrides it for edge cases |
| Reviewer self-discovers + fixes hallucinations | Shallow detection — reviewer is distracted by grammar/style |
| Dedicated Fact Checker → Reviewer gets explicit report | **Systematic detection, targeted correction** |

The Fact Checker has one narrow job (structured output, no creativity required) — this works reliably on 7B local models. The Reviewer then applies explicit violations rather than trying to discover them independently.

---

## LLM Options

| Provider | `.env` variable | Cost | Speed |
|---|---|---|---|
| **Ollama** (default) | — (no key needed) | Free | ~3–5 min |
| **DeepSeek** | `DEEPSEEK_API_KEY` | ~$0.002/run | ~1–2 min |
| **Claude (Anthropic)** | `ANTHROPIC_API_KEY` | Haiku ~$0.01, Sonnet ~$0.05 | ~1 min |
| **ChatGPT (OpenAI)** | `OPENAI_API_KEY` | 4o-mini ~$0.01, 4o ~$0.05 | ~1 min |
| **Gemini (Google)** | `GOOGLE_API_KEY` | Flash ~$0.01 (free tier quota is very low for 8-call pipelines) | ~1 min |
| **Groq** | `GROQ_API_KEY` | Free tier available | ~30 sec |
| **OpenRouter** | `OPENROUTER_API_KEY` | Many free models | Varies |

The Streamlit sidebar lets you switch provider, pick a model, enter your API key, and tune temperature and max tokens — all at runtime without touching any code.

> **Note on Claude:** The Anthropic API (`ANTHROPIC_API_KEY`) is a **separate product** from a Claude.ai or Claude Code subscription. Having a Claude Code subscription does not give you API access — you need a key from [console.anthropic.com](https://console.anthropic.com). For cheapest Claude quality, use `claude-haiku-4-5` (~$0.01/run).

**Recommended local models:**

| Model | VRAM | Notes |
|---|---|---|
| `qwen3.5:9b` (default) | ~6 GB | Best instruction following; thinking mode auto-disabled by the app |
| `qwen3:8b` | ~6 GB | Previous default — still excellent |
| `qwen2.5:7b` | ~6 GB | Solid structured-output following |
| `mistral:7b` | ~5 GB | Good English writing quality |
| `llama3.2:3b` | ~3 GB | Fastest, lower quality |
| `qwen3:14b` / `qwen2.5:14b` | ~10 GB | Better reasoning, fewer hallucinations |

The app sets a 16k context window (`num_ctx`) for Ollama automatically — the Ollama default (~2–4k) would silently truncate the pipeline's long prompts. Because Ollama's OpenAI-compatible endpoint ignores per-request options, the app creates a lightweight derived model on first use (e.g. `qwen3.5:9b-ctx16384` — it shares the base weights, no extra disk). Override the size with `OLLAMA_NUM_CTX` in `.env` if you are RAM-constrained (`OLLAMA_NUM_CTX=0` disables the derived model).

---

## Minimum Hardware for Local AI (Ollama)

Running a 7B model locally is more accessible than people expect — no GPU required.

### Minimum (CPU-only, 7B model)

| Component | Minimum | Notes |
|---|---|---|
| RAM | **16 GB** | 8 GB technically works but generation is very slow and may crash |
| CPU | Intel i5 (10th gen+) / AMD Ryzen 5 (5000+) | Any modern quad-core |
| Storage | **10 GB free** | Model files are 4–8 GB each |
| GPU | Not required | CPU-only works fine |
| OS | Windows 10/11, macOS 12+, Ubuntu 20.04+ | |

Expect **1–3 tokens/sec** on CPU-only. A full 8-agent pipeline takes 5–10 minutes.

### Recommended (GPU-accelerated)

| Component | Recommended |
|---|---|
| RAM | 32 GB |
| GPU | NVIDIA RTX 3060 (8 GB VRAM) or better |
| Storage | 50 GB free (room for multiple models) |

GPU gives **10–30 tokens/sec** — pipeline runs in under 2 minutes, same as a cloud API.

**Apple Silicon (M1/M2/M3/M4):** Ollama uses Metal acceleration natively. An M2 MacBook Pro with 16 GB unified memory runs 7B models at ~20–30 tokens/sec — excellent local performance with no GPU required.

---

## File Structure

```
cover_letter_crew/
├── app.py                  # Streamlit web UI
├── cover_letter_crew.py    # All agents, tasks, and public generate_cover_letters() API
├── file_parser.py          # PDF / DOCX / TXT text extraction
├── profile.py              # Default candidate profile (pre-loads in UI)
├── requirements.txt        # Python dependencies
├── Run_Streamlit.bat       # Windows double-click launcher (Streamlit)
├── Run.bat                 # Windows double-click launcher (CLI)
├── .env                    # API keys — never commit this
├── .env.example            # Template for .env
└── output/                 # Generated .docx files
```

### Key public functions in `cover_letter_crew.py`

```python
generate_cover_letters(profile_text, job_raw, llm_config, ...)
# → dict with en_formal, en_modern, job, docx_path, profile_parsed

build_crew(job, llm, profile_text, step_callback, candidate_name, structured_profile, industry)
# → CrewAI Crew with 7 agents and 8 tasks

clean_job_paste(raw, llm, interactive=True)
# → dict: company, job_title, location, job_desc, ref_number

parse_profile(profile_text, llm)
# → structured dict: name, companies, skills, tools, education, languages

get_llm(llm_config)
# → LLM instance (Ollama, DeepSeek, Anthropic, OpenAI, Gemini, Groq, OpenRouter)

save_to_docx(job, results, output_dir, candidate_name, ...)
# → Path to saved .docx file (2 variants)
```

---

## Usage

### Web UI (recommended)
```bash
streamlit run app.py
```
1. Upload your CV (PDF, DOCX, or TXT) or paste profile text
2. Paste the full job page from LinkedIn or any job board
3. Click **Generate Cover Letters**
4. Copy from the tabs, or download the Word document

### CLI (original mode)
```bash
python cover_letter_crew.py
```
Paste the job page when prompted. Word file is saved to `./output/`.

### Programmatic API
```python
from cover_letter_crew import generate_cover_letters

results = generate_cover_letters(
    profile_text="... your CV text ...",
    job_raw="... raw job posting ...",
    llm_config={
        "backend":     "ollama",
        "model":       "qwen3.5:9b",
        "temperature": 0.7,
        "max_tokens":  1500,
    },
    save_docx=True,
)

print(results["en_formal"])   # English formal letter body
print(results["en_modern"])   # English modern letter body
```

---

## Prompt Design for 7B Models

Small models work best when prompts are:
- **Structured** — output format uses explicit labels (`TECHNICAL_REQUIREMENTS:`, `PROHIBITED_CLAIMS:`, `EN_FORMAL_VIOLATIONS:`)
- **Direct** — task descriptions use numbered imperatives, not open-ended questions
- **Constrained** — writers are told: *"Output ONLY the cover letter body text."*
- **Narrow** — the Fact Checker has one job (find violations), which actually works better on smaller models than asking a single agent to write + fact-check simultaneously

If output quality is inconsistent:
1. Lower temperature to `0.5` in the sidebar
2. Reduce max tokens to `1000–1200`
3. Switch to `qwen3:14b` or the DeepSeek API

---

## Diagnostics

If anything misbehaves — especially with local AI — run the built-in step-by-step health check:

**Windows (batch file — no venv activation needed):**
```bat
.\diagnose.bat               REM steps 1–12: unit tests + live Ollama/LLM checks (~2 min)
.\diagnose.bat --offline     REM steps 1–3 only: no Ollama needed
.\diagnose.bat --full        REM adds a real end-to-end 8-task pipeline run (slow!)
.\diagnose.bat --model qwen3:8b
.\diagnose.bat --url http://other-host:11434
```

**Mac / Linux / manual venv:**
```bash
python diagnose.py              # steps 1–12: unit tests + live Ollama/LLM checks (~2 min)
python diagnose.py --offline    # steps 1–3 only: no Ollama needed
python diagnose.py --full       # adds a real end-to-end 8-task pipeline run (slow!)
python diagnose.py --model qwen3:8b
python diagnose.py --url http://other-host:11434
```

Each numbered step prints `[PASS]` / `[FAIL]` with a hint, covering: environment & packages → project files → 41 unit tests (think-stripping, JSON extraction, match score, industry detection, letter validation, docx export, …) → Ollama server → model pulled → basic LLM call → context window actually applied → JSON mode → thinking-mode control → quick match → job parsing → profile parsing → (with `--full`) complete letter generation.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: crewai` | Activate the virtual environment first |
| `Connection refused` (Ollama) | Run `ollama serve` in a separate terminal |
| Model not found | Run `ollama pull qwen3.5:9b` |
| Empty letter body | Check API key is valid; try a larger model |
| Slow generation | Switch to DeepSeek API (~$0.002) for 3× speed |
| Out of memory | Use `llama3.2:3b` or reduce max tokens in sidebar |
| Fact Checker shows many violations | Good — the Reviewer will fix them. If final letter still has issues, try a larger model |

---

## Cost Reference

| Model | Cost per full run |
|---|---|
| Ollama local | $0.00 |
| DeepSeek V3 | ~$0.001–0.003 |
| Claude Haiku 4.5 | ~$0.01–0.02 |
| Claude Sonnet 4.6 | ~$0.05–0.10 |

---

## License

MIT — see [LICENSE](LICENSE).

Free to use, modify, and share. If you build something useful on top of this, a mention is appreciated but not required.

---

## About

Built as a hands-on experiment in agentic AI.

Stack: Python · [CrewAI](https://crewai.com) · [Streamlit](https://streamlit.io) · [LiteLLM](https://litellm.ai) · [Ollama](https://ollama.com)
