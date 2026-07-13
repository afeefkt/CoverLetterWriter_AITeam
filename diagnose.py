"""
============================================================
  DIAGNOSE — step-by-step health check for Cover Letter Crew
============================================================

Run inside the project venv:

    python diagnose.py              # steps 1-12 (offline checks + small live LLM calls)
    python diagnose.py --offline    # steps 1-3 only (no Ollama / no LLM calls)
    python diagnose.py --full       # everything + a real end-to-end pipeline run (slow!)
    python diagnose.py --model qwen3:8b        # test a different local model
    python diagnose.py --url http://host:11434 # test a different Ollama server

Every step prints [PASS] / [FAIL] / [WARN] / [SKIP] with a hint on failure,
so you can see exactly which stage of the local-AI pipeline is broken.
Exit code is 0 when there are no FAILs.
"""

import argparse
import io
import json
import os
import sys
import time
import urllib.request

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")  # silence remote fetch (SSL noise)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── result bookkeeping ───────────────────────────────────────────────────────

RESULTS = {"PASS": 0, "FAIL": 0, "WARN": 0, "SKIP": 0}
FAILED_STEPS = []


def report(status: str, step: str, detail: str = "", hint: str = ""):
    RESULTS[status] += 1
    line = f"[{status}] {step}"
    if detail:
        line += f" — {detail}"
    print(line)
    if hint and status in ("FAIL", "WARN"):
        print(f"       hint: {hint}")
    if status == "FAIL":
        FAILED_STEPS.append(step)


def section(title: str):
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


# ── sample data used by the live steps ───────────────────────────────────────

SAMPLE_JD = """\
Embedded Software Engineer - Motor Control (m/f/d)
Company: Example Motors GmbH
Location: Regensburg, Germany
Reference: EM-2026-042

Your tasks:
- Develop AUTOSAR-compliant motor control software for electric drives (ASIL-B)
- Model-based development with MATLAB/Simulink and Embedded Coder
- MIL/SIL validation and unit testing of control algorithms
- Integration testing with CANoe and debugging on real hardware

Your profile:
- Degree in engineering or computer science
- Experience with Embedded C, MISRA C and ISO 26262
- Knowledge of FOC motor control is a plus
- English fluent, German B1 or better
"""

SAMPLE_CV = """\
Name: Alex Miller
Embedded Software Engineer | Motor Control | AUTOSAR

Working Experience

Sep'24-Now  Embedded SW Development Engineer, Example Systems GmbH, Regensburg
- Developed model-based FOC algorithms for 6-phase motors; generated AUTOSAR
  ASIL-B compliant code via Embedded Coder.
- Built plant model and MIL environment for performance validation.
- Automated unit test generation with Python; CI/CD pipeline setup.
Tools: MATLAB/Simulink, Embedded Coder, CANoe, Git, Python, C

Jul'21-Aug'24  Model Based Design Engineer, Tech Ltd., Bangalore
- Developed AUTOSAR-based EPS components with Simulink/Stateflow.
- Static analysis with Polyspace; compliance with ISO 26262, MISRA C.
Tools: MATLAB/Simulink, Polyspace, Embedded Coder, DOORS, Git, JIRA

Education: Bachelor in Mechanical Engineering, 2015
Languages: English (fluent), German (B1)
"""


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 1 — environment
# ═════════════════════════════════════════════════════════════════════════════

def step_1_environment():
    section("STEP 1 — Python environment & packages")
    v = sys.version_info
    if v >= (3, 10):
        report("PASS", "Python version", f"{sys.version.split()[0]}")
    else:
        report("FAIL", "Python version", f"{sys.version.split()[0]} (< 3.10)",
               "recreate the venv with Python 3.10+ (see setup.bat)")

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    report("PASS" if in_venv else "WARN", "Running inside a virtualenv",
           sys.prefix, "activate .venv first: .venv\\Scripts\\activate")

    for pkg, mod in [("crewai", "crewai"), ("litellm", "litellm"),
                     ("streamlit", "streamlit"), ("python-docx", "docx"),
                     ("pypdf", "pypdf"), ("python-dotenv", "dotenv")]:
        try:
            m = __import__(mod)
            ver = getattr(m, "__version__", "?")
            report("PASS", f"import {pkg}", f"version {ver}")
        except Exception as e:
            report("FAIL", f"import {pkg}", str(e),
                   f"pip install -r requirements.txt (missing: {pkg})")


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 2 — project files & config
# ═════════════════════════════════════════════════════════════════════════════

def step_2_project():
    section("STEP 2 — Project files & configuration")
    from pathlib import Path
    here = Path(__file__).parent
    for f in ["app.py", "cover_letter_crew.py", "file_parser.py", "requirements.txt"]:
        if (here / f).exists():
            report("PASS", f"file present: {f}")
        else:
            report("FAIL", f"file present: {f}", "missing", "restore it from git")

    try:
        import cover_letter_crew as clc
        report("PASS", "import cover_letter_crew", f"default model = {clc.OLLAMA_MODEL}")
        report("PASS", "config OLLAMA_NUM_CTX", str(clc.OLLAMA_NUM_CTX))
    except Exception as e:
        report("FAIL", "import cover_letter_crew", str(e),
               "fix the import error above before anything else")
        return None
    return clc


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 3 — offline unit tests (no LLM, no Ollama)
# ═════════════════════════════════════════════════════════════════════════════

def step_3_unit_tests(clc):
    section("STEP 3 — Offline unit tests (pure functions)")

    def unit(desc, cond, hint=""):
        report("PASS" if cond else "FAIL", desc, "", hint)

    # 3a. think-block stripping (qwen3 / qwen3.5 thinking mode)
    unit("3a strip_think: closed block removed",
         clc._strip_think("<think>reasoning</think>Hello") == "Hello")
    unit("3a strip_think: unclosed block removed (truncated output)",
         clc._strip_think("Answer<think>cut off") == "Answer")
    unit("3a strip_think: plain text untouched",
         clc._strip_think("Dear Hiring Manager,") == "Dear Hiring Manager,")

    # 3b. no-think suffix routing
    unit("3b /no_think suffix for qwen3:8b (soft switch supported)",
         clc._no_think_suffix({"backend": "ollama", "model": "qwen3:8b"}) == " /no_think")
    unit("3b NO suffix for qwen3.5 (switch removed; API-level instead)",
         clc._no_think_suffix({"backend": "ollama", "model": "qwen3.5:9b"}) == "")
    unit("3b no suffix for qwen2.5 / cloud",
         clc._no_think_suffix({"backend": "ollama", "model": "qwen2.5:7b"}) == ""
         and clc._no_think_suffix({"backend": "anthropic", "model": "claude-haiku-4-5"}) == "")
    unit("3b reasoning disabled at API level for qwen3 family",
         clc._ollama_extra_body("qwen3.5:9b").get("reasoning_effort") == "none"
         and clc._ollama_extra_body("qwen3:8b").get("reasoning_effort") == "none")
    unit("3b reasoning field NOT sent for non-thinking models",
         "reasoning_effort" not in clc._ollama_extra_body("qwen2.5:7b"))
    unit("3b num_ctx always in extra body",
         clc._ollama_extra_body("qwen3.5:9b")["options"]["num_ctx"] == clc.OLLAMA_NUM_CTX)

    # 3c. model size detection → compact prompts
    unit("3c size parse qwen3.5:9b -> 9.0", clc._model_size_b("qwen3.5:9b") == 9.0)
    unit("3c size parse qwen3:0.6b -> 0.6", clc._model_size_b("qwen3:0.6b") == 0.6)
    unit("3c qwen3.5:9b gets compact prompts",
         clc._is_small_local_model({"backend": "ollama", "model": "qwen3.5:9b"}))
    unit("3c qwen3:14b gets full prompts",
         not clc._is_small_local_model({"backend": "ollama", "model": "qwen3:14b"}))

    # 3d. localhost detection (auto-start guard)
    unit("3d localhost detected", clc._is_localhost("http://localhost:11434"))
    unit("3d remote host detected", not clc._is_localhost("http://192.168.1.50:11434"))

    # 3e. robust JSON extraction (job parser fallback)
    unit("3e json: direct", clc._robust_json_extract('{"a": 1}') == {"a": 1})
    unit("3e json: fenced",
         clc._robust_json_extract('```json\n{"a": 1}\n```') == {"a": 1})
    unit("3e json: embedded in prose",
         clc._robust_json_extract('Here you go: {"a": {"b": 2}} hope it helps')
         == {"a": {"b": 2}})
    unit("3e json: garbage -> None", clc._robust_json_extract("no json here") is None)

    # 3f. reviewer output parsing
    parsed = clc._parse_reviewer_output(
        "<think>meta</think>EN_FORMAL_FINAL:\nFormal text here.\n\n"
        "EN_MODERN_FINAL:\nModern text here.")
    unit("3f reviewer labels parsed",
         parsed["en_formal"].startswith("Formal") and parsed["en_modern"].startswith("Modern"))

    # 3g. match score computation
    gap = ("STRONG_MATCHES:\n1. a\n2. b\n3. c\n\nPARTIAL_MATCHES:\n1. d\n\n"
           "PROHIBITED_CLAIMS:\nx\n\nSAFE_FRAMING:\ny")
    unit("3g match score = 70 for 3 strong/1 partial/1 gap",
         clc._compute_match_score(gap) == 70)

    # 3h. industry auto-detection
    job = {"job_title": "Embedded Software Engineer", "job_desc": SAMPLE_JD,
           "company": "Example Motors GmbH", "location": "Regensburg"}
    unit("3h industry detect -> Automotive & Embedded",
         clc.infer_industry(job) == "Automotive & Embedded",
         "check _INDUSTRY_KEYWORDS in cover_letter_crew.py")
    unit("3h 'Auto-detect' sentinel resolved",
         clc._resolve_industry("Auto-detect (from JD)", job) == "Automotive & Embedded")

    # 3i. letter sanitize + validate + name extraction
    unit("3i sanitize removes fences/labels/bold",
         clc._sanitize_letter("```\nEN_FORMAL_FINAL:\n**Dear** X\n```") == "Dear X")
    good = ("Dear Hiring Manager,\n\n" + ("Sentence about AUTOSAR work. " * 3 + "\n\n") * 3
            + "Kind regards,\nAlex Miller")
    unit("3i good letter passes validation",
         clc._validate_letter(good, "Alex Miller") == [])
    bad_issues = clc._validate_letter("Hi.\nShort.", "Alex Miller")
    unit("3i broken letter is flagged", len(bad_issues) >= 1)
    unit("3i banned phrase flagged",
         any("banned" in i for i in
             clc._validate_letter(good + "\n\nI am passionate about this.", "Alex Miller")))
    unit("3i name extraction from labelled CV",
         clc._extract_name_from_profile(SAMPLE_CV) == "Alex Miller")

    # 3j. file parser (txt path — pdf/docx need binary fixtures)
    from file_parser import extract_text
    txt = extract_text(io.BytesIO("hello resume".encode()), "cv.txt")
    unit("3j file_parser txt extraction", txt == "hello resume")

    # 3k. docx builder returns a real .docx (ZIP magic bytes "PK")
    data = clc.build_docx_bytes(
        {"company": "Example Motors GmbH", "job_title": "Embedded SW Engineer",
         "location": "Regensburg", "ref_number": "EM-2026-042"},
        [("English - Formal", good)],
        candidate_name="Alex Miller")
    unit("3k build_docx_bytes produces valid docx", data[:2] == b"PK")


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 4-5 — Ollama server & model availability
# ═════════════════════════════════════════════════════════════════════════════

def step_4_ollama_server(clc, url):
    section("STEP 4 — Ollama server")
    if clc._ollama_is_running(url):
        try:
            with urllib.request.urlopen(f"{url}/api/version", timeout=3) as r:
                ver = json.loads(r.read()).get("version", "?")
        except Exception:
            ver = "?"
        report("PASS", "Ollama server reachable", f"{url} (version {ver})")
        return True
    report("FAIL", "Ollama server reachable", url,
           "start it with 'ollama serve' (the app can also auto-start it on localhost)")
    return False


def step_5_model(clc, url, model):
    section(f"STEP 5 — Model availability: {model}")
    if clc._model_is_pulled(model, url):
        report("PASS", f"model '{model}' is pulled")
        return True
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=5) as r:
            names = [m["name"] for m in json.loads(r.read()).get("models", [])]
    except Exception:
        names = []
    report("FAIL", f"model '{model}' is pulled",
           f"available: {', '.join(names) or 'none'}",
           f"run: ollama pull {model}  (the Streamlit app would auto-pull it on Generate)")
    return False


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 6-9 — live LLM plumbing
# ═════════════════════════════════════════════════════════════════════════════

def _cfg(model, url, **over):
    cfg = {"backend": "ollama", "model": model, "base_url": url,
           "temperature": 0.2, "max_tokens": 200}
    cfg.update(over)
    return cfg


def step_6_basic_call(clc, model, url):
    section("STEP 6 — Basic LLM call through get_llm()")
    t0 = time.time()
    try:
        llm = clc.get_llm(_cfg(model, url))
        raw = llm.call([{"role": "user",
                         "content": "Reply with exactly the word OK and nothing else."
                                    + clc._no_think_suffix(_cfg(model, url))}])
        out = clc._strip_think(str(raw))
        dt = time.time() - t0
        if out:
            report("PASS", "LLM responds", f"'{out[:40]}' in {dt:.1f}s")
            return True
        report("FAIL", "LLM responds", "empty response after think-stripping",
               "raise max_tokens, or the model may be emitting only reasoning")
    except Exception as e:
        report("FAIL", "LLM responds", f"{type(e).__name__}: {e}",
               "this is the crewai->OpenAI-SDK->Ollama path; check the error above")
    return False


def step_7_num_ctx(clc, url):
    section("STEP 7 — num_ctx (context window) actually applied")
    # /api/ps reports the context length of the currently loaded model instance —
    # it reflects the options sent with the last request (step 6).
    try:
        with urllib.request.urlopen(f"{url}/api/ps", timeout=5) as r:
            models = json.loads(r.read()).get("models", [])
        if not models:
            report("WARN", "num_ctx verification", "no model currently loaded",
                   "run step 6 first (default run order does this automatically)")
            return
        m = models[0]
        ctx = m.get("context_length")
        if ctx is None:
            report("WARN", "num_ctx verification",
                   "this Ollama version does not report context_length in /api/ps",
                   "check the ollama server log: it prints the n_ctx used per load")
        elif int(ctx) >= clc.OLLAMA_NUM_CTX:
            report("PASS", "num_ctx applied", f"loaded with context_length={ctx} "
                   f"(configured {clc.OLLAMA_NUM_CTX})")
        else:
            report("FAIL", "num_ctx applied",
                   f"loaded context_length={ctx} < configured {clc.OLLAMA_NUM_CTX}",
                   "extra_body options are not reaching Ollama — long prompts will be truncated")
    except Exception as e:
        report("WARN", "num_ctx verification", f"{type(e).__name__}: {e}")


def step_8_json_mode(clc, model, url):
    section("STEP 8 — JSON mode (structured extraction calls)")
    try:
        llm = clc.get_llm(_cfg(model, url), json_mode=True)
        raw = llm.call([{"role": "user",
                         "content": 'Return a JSON object: {"status": "ok"}'
                                    + clc._no_think_suffix(_cfg(model, url))}])
        data = clc._robust_json_extract(clc._strip_think(str(raw)))
        if isinstance(data, dict):
            report("PASS", "JSON mode returns parseable JSON", json.dumps(data)[:60])
        else:
            report("FAIL", "JSON mode returns parseable JSON", f"got: {str(raw)[:80]}",
                   "response_format json_object not honored — extraction falls back to regex")
    except Exception as e:
        report("FAIL", "JSON mode call", f"{type(e).__name__}: {e}")


def step_9_thinking(clc, model, url):
    section("STEP 9 — Thinking-mode control (reasoning disabled for qwen3 family)")
    if "qwen3" not in model.lower():
        report("SKIP", "thinking-mode check", f"'{model}' is not a qwen3-family model")
        return
    # With a tight token budget, an actual answer can only appear if thinking is
    # really off — otherwise reasoning consumes the whole budget and content is empty.
    try:
        t0 = time.time()
        llm = clc.get_llm(_cfg(model, url, max_tokens=100))
        raw = str(llm.call([{"role": "user",
                             "content": "What is 2+2? Answer with just the number."
                                        + clc._no_think_suffix(_cfg(model, url))}]))
        out = clc._strip_think(raw)
        dt = time.time() - t0
        if "<think" in raw.lower():
            report("WARN", "thinking leaked inline but stripping cleans it",
                   f"'{out[:30]}'", "reasoning_effort not honored; relying on _strip_think()")
        elif "4" in out:
            report("PASS", "thinking disabled — direct answer within tight budget",
                   f"'{out[:30]}' in {dt:.1f}s")
        else:
            report("FAIL", "thinking-mode control",
                   f"no usable answer in 100 tokens (got '{out[:40]}')",
                   "reasoning is still consuming the budget — check that "
                   "reasoning_effort='none' reaches Ollama (needs Ollama >= 0.11 or so)")
    except Exception as e:
        report("FAIL", "thinking-mode check", f"{type(e).__name__}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 10-12 — pipeline building blocks on sample data
# ═════════════════════════════════════════════════════════════════════════════

def step_10_quick_match(clc, model, url):
    section("STEP 10 — quick_match_check (single-call pre-analysis)")
    try:
        t0 = time.time()
        r = clc.quick_match_check(SAMPLE_CV, SAMPLE_JD, _cfg(model, url, max_tokens=600))
        dt = time.time() - t0
        ok_score = isinstance(r.get("match_score"), int) and 0 <= r["match_score"] <= 100
        ok_job = bool(r.get("job", {}).get("job_title"))
        report("PASS" if ok_score else "FAIL", "match score parsed",
               f"{r.get('match_score')}% in {dt:.0f}s",
               "model did not follow the MATCH_SCORE output format")
        report("PASS" if ok_job else "WARN", "job metadata extracted",
               f"title='{r['job'].get('job_title','')}' company='{r['job'].get('company','')}'",
               "model did not extract JOB_TITLE/COMPANY lines")
        report("PASS" if (r.get("strong") or r.get("gaps")) else "WARN",
               "strengths/gaps extracted",
               f"{len(r.get('strong', []))} strong / {len(r.get('gaps', []))} gaps")
    except Exception as e:
        report("FAIL", "quick_match_check", f"{type(e).__name__}: {e}")


def step_11_job_parse(clc, model, url):
    section("STEP 11 — clean_job_paste (job page parser agent)")
    try:
        cfg = _cfg(model, url, max_tokens=1200)
        llm = clc.get_llm(cfg, json_mode=True)
        t0 = time.time()
        job = clc.clean_job_paste(SAMPLE_JD, llm, interactive=False,
                                  no_think=clc._no_think_suffix(cfg))
        dt = time.time() - t0
        ok = "example motors" in (job.get("company") or "").lower()
        report("PASS" if ok else "WARN", "company extracted",
               f"'{job.get('company')}' in {dt:.0f}s",
               "parser did not find the company; app falls back to manual entry / override")
        report("PASS" if job.get("job_desc") else "FAIL", "job description kept",
               f"{len(job.get('job_desc', ''))} chars")
    except Exception as e:
        report("FAIL", "clean_job_paste", f"{type(e).__name__}: {e}")


def step_12_profile_parse(clc, model, url):
    section("STEP 12 — parse_profile (structured CV extraction)")
    try:
        cfg = _cfg(model, url, max_tokens=1500)
        llm = clc.get_llm(cfg, json_mode=True)
        t0 = time.time()
        p = clc.parse_profile(SAMPLE_CV, llm, no_think=clc._no_think_suffix(cfg))
        dt = time.time() - t0
        if "raw" in p:
            report("WARN", "profile parsed to JSON",
                   f"fell back to raw text in {dt:.0f}s",
                   "model returned invalid JSON; pipeline still works with raw text")
        else:
            ok = bool(p.get("companies"))
            report("PASS" if ok else "WARN", "profile parsed to JSON",
                   f"name='{p.get('name')}', {len(p.get('companies', []))} companies in {dt:.0f}s")
    except Exception as e:
        report("FAIL", "parse_profile", f"{type(e).__name__}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
#  STEP 13 — full pipeline (only with --full)
# ═════════════════════════════════════════════════════════════════════════════

def step_13_full_pipeline(clc, model, url):
    section("STEP 13 — FULL 8-task pipeline (this takes minutes on local AI)")
    calls = []
    try:
        t0 = time.time()
        results = clc.generate_cover_letters(
            profile_text=SAMPLE_CV,
            job_raw=SAMPLE_JD,
            llm_config=_cfg(model, url, max_tokens=2000, temperature=0.7),
            step_callback=lambda out: calls.append(out),
            candidate_name="Alex Miller",
            industry="Auto-detect (from JD)",
        )
        dt = time.time() - t0
        report("PASS" if len(calls) == 8 else "WARN", "task callbacks fired",
               f"{len(calls)}/8 in {dt:.0f}s total",
               "task_callback should fire exactly once per task")
        for key in ("en_formal", "en_modern"):
            letter = results.get(key, "")
            issues = clc._validate_letter(letter, "Alex Miller")
            if letter and not issues:
                report("PASS", f"{key} letter valid", f"{len(letter)} chars")
            elif letter:
                report("WARN", f"{key} letter has issues", "; ".join(issues)[:120])
            else:
                report("FAIL", f"{key} letter", "empty",
                       "check the crew log above for the failing task")
        report("PASS" if "<think" not in (results.get("en_formal", "")
               + results.get("en_modern", "")).lower() else "FAIL",
               "no thinking leak in final letters")
        report("PASS" if results.get("industry") == "Automotive & Embedded" else "WARN",
               "industry auto-detected", str(results.get("industry")))
        data = clc.build_docx_bytes(results["job"],
                                    [("English - Formal", results["en_formal"])],
                                    candidate_name="Alex Miller")
        report("PASS" if data[:2] == b"PK" else "FAIL", "docx export from results")
    except Exception as e:
        report("FAIL", "full pipeline", f"{type(e).__name__}: {e}")


# ═════════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="Cover Letter Crew diagnostics")
    ap.add_argument("--offline", action="store_true", help="skip Ollama / LLM steps")
    ap.add_argument("--full", action="store_true", help="also run the full 8-task pipeline")
    ap.add_argument("--model", default=None, help="local model to test (default: app default)")
    ap.add_argument("--url", default=None, help="Ollama base URL (default: http://localhost:11434)")
    args = ap.parse_args()

    step_1_environment()
    clc = step_2_project()
    if clc is None:
        print("\nCannot continue without cover_letter_crew — fix the failures above.")
        sys.exit(1)

    step_3_unit_tests(clc)

    model = args.model or clc.OLLAMA_MODEL
    url = args.url or clc.OLLAMA_BASE_URL

    if args.offline:
        report("SKIP", "steps 4-13 (offline mode)")
    else:
        server_ok = step_4_ollama_server(clc, url)
        model_ok = step_5_model(clc, url, model) if server_ok else False
        if server_ok and model_ok:
            if step_6_basic_call(clc, model, url):
                step_7_num_ctx(clc, url)
                step_8_json_mode(clc, model, url)
                step_9_thinking(clc, model, url)
                step_10_quick_match(clc, model, url)
                step_11_job_parse(clc, model, url)
                step_12_profile_parse(clc, model, url)
                if args.full:
                    step_13_full_pipeline(clc, model, url)
                else:
                    report("SKIP", "step 13 full pipeline", "re-run with --full to include it")
            else:
                report("SKIP", "steps 7-13", "basic LLM call failed")
        else:
            report("SKIP", "steps 6-13", "server or model unavailable")

    section("SUMMARY")
    print(f"  PASS: {RESULTS['PASS']}   FAIL: {RESULTS['FAIL']}   "
          f"WARN: {RESULTS['WARN']}   SKIP: {RESULTS['SKIP']}")
    if FAILED_STEPS:
        print("\n  Failed steps:")
        for s in FAILED_STEPS:
            print(f"   - {s}")
    else:
        print("\n  All executed checks passed.")
    sys.exit(1 if FAILED_STEPS else 0)


if __name__ == "__main__":
    main()
