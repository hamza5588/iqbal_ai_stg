#!/usr/bin/env python3
"""Standalone IqbalAI router/executor QA audit. Does not import or modify the app."""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PDF_DIR = ROOT / "pdfs"
OUT = ROOT / "results.json"

# --- Exact copies of production regex classifiers (app/utils/rag_service.py) ---
LESSON_INTENT_PATTERNS = (
    r"\bcreate\b.*\blesson\b", r"\bgenerate\b.*\blesson\b", r"\bmake\b.*\blesson\b",
    r"\bwrite\b.*\blesson\b", r"\bbuild\b.*\blesson\b", r"\blesson\s*plan\b",
    r"\bcreate\b.*\blecture\b", r"\bgenerate\b.*\blecture\b", r"\bmake\b.*\blecture\b",
    r"\bfull\s+lesson\b", r"\bneed\b.*\blecture\b", r"\bgive\b.*\blecture\b",
    r"\blecture\s+on\b", r"\bprepare\b.*\blecture\b", r"\bwant\b.*\blecture\b",
    r"\bwant\b.*\blesson\b", r"\bgive\b.*\blesson\b", r"\bprepare\b.*\blesson\b",
    r"\bteach\b.*\blecture\b", r"\bteach\b.*\blesson\b", r"\bi\s+need\b.*\blecture\b",
    r"\bi\s+need\b.*\blesson\b",
)
OWN_ANSWER_PATTERNS = (
    r"\bexplain\s+why\b", r"\bhow\s+did\s+you\s+get\b", r"\bwhy\s+did\s+you\s+use\b",
    r"\bwhy\s+is\s+it\b", r"\bwhat\s+does\s+.*\s+mean\b", r"\bwhy\s+not\b",
    r"\bwhere\s+did\s+.*\s+come\s+from\b",
)
META_PATTERNS = (
    r"\bwhat\s+(did|do)\s+i\s+ask\b", r"\bwhat\s+i\s+ask\b", r"\blast\s+question\b",
    r"\bpaste\s+exactly\b", r"\bwhat\s+were\s+my\s+(last\s+)?\d*\s*questions?\b",
)
VAGUE_SINGLE = {
    "explain", "what", "why", "how", "help", "yes", "ok", "no", "thanks",
    "hi", "hello", "hey", "please",
}
AFFIRMATIVE_RE = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok(ay)?|please\s*do|go\s*ahead|do\s*it|of\s*course|"
    r"sounds?\s*good|please\s*answer|answer\s*it|please)\b",
    re.IGNORECASE,
)
NEGATIVE_RE = re.compile(
    r"^\s*(no|nope|nah|n/?a|don'?t|do\s+not|never\s*mind|nevermind|skip\s*it|not\s*now|no\s*thanks)\b",
    re.IGNORECASE,
)


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def is_lesson_creation(text):
    n = _norm(text)
    return bool(n) and any(re.search(p, n) for p in LESSON_INTENT_PATTERNS)


def is_own_answer(text):
    n = _norm(text)
    return bool(n) and any(re.search(p, n) for p in OWN_ANSWER_PATTERNS)


def is_meta(text):
    n = _norm(text)
    return bool(n) and any(re.search(p, n) for p in META_PATTERNS)


def is_underspecified(text):
    t = (text or "").strip().lower()
    if not t:
        return True
    words = t.split()
    if len(words) >= 2:
        return False
    return words[0].rstrip("?.!").lower() in VAGUE_SINGLE


def fallback_intent(text):
    if is_meta(text):
        return "meta_conversation"
    if is_lesson_creation(text):
        return "lesson_generation"
    if is_own_answer(text):
        return "own_answer_followup"
    if is_underspecified(text):
        return "clarification"
    return "document_qa"


def classify_yes_no(text):
    t = (text or "").strip()
    if not t:
        return None
    if NEGATIVE_RE.match(t):
        return "no"
    if AFFIRMATIVE_RE.match(t):
        return "yes"
    return None


def select_intent_tool_names(intent):
    """Exact copy of local _select_intent_tool_names."""
    if intent == "lesson_save":
        return ("finalize_lesson_tool",)
    if intent == "lesson_modification":
        return (
            "update_lesson_tool", "teach_topic_tool", "rag_tool",
            "get_page_tool", "list_topics_whole_doc_tool",
        )
    if intent == "lesson_generation":
        return (
            "teach_topic_tool", "rag_tool", "get_page_tool",
            "list_topics_whole_doc_tool", "update_lesson_tool",
        )
    if intent in (
        "meta_conversation", "own_answer_followup", "greeting_casual", "clarification",
    ):
        return ()
    return None  # full catalog


FULL_TOOLS = (
    "calculator", "rag_tool", "get_page_tool", "list_topics_whole_doc_tool",
    "teach_topic_tool", "count_pdf_words_tool", "count_words_in_text_tool",
    "update_lesson_tool", "finalize_lesson_tool",
)

EXPECTED_TOOLS = {
    "document_qa": {"must": {"rag_tool"}, "must_not": {"finalize_lesson_tool"}},
    "lesson_generation": {"must": {"teach_topic_tool", "update_lesson_tool"}, "must_not": {"finalize_lesson_tool"}},
    "lesson_modification": {"must": {"update_lesson_tool"}, "must_not": {"finalize_lesson_tool"}},
    "lesson_save": {"must": {"finalize_lesson_tool"}, "must_not": {"rag_tool", "teach_topic_tool"}},
    "meta_conversation": {"must": set(), "must_not": {"rag_tool", "teach_topic_tool", "finalize_lesson_tool"}},
    "own_answer_followup": {"must": set(), "must_not": {"rag_tool", "finalize_lesson_tool"}},
    "greeting_casual": {"must": set(), "must_not": {"rag_tool"}},
    "clarification": {"must": set(), "must_not": {"rag_tool"}},
    "general_knowledge_qa": {"must": set(), "must_not": {"finalize_lesson_tool"}},
    "lesson_qa": {"must": set(), "must_not": {"finalize_lesson_tool"}},
}


def resolve_tools(intent):
    names = select_intent_tool_names(intent)
    if names is None:
        return list(FULL_TOOLS)
    return list(names)


# Gold-labeled utterance matrix (expected = LLM-router intent we would require)
CASES = []


def add(cat, text, expected, notes=""):
    CASES.append({"category": cat, "text": text, "expected": expected, "notes": notes})


# A. Document QA
for t in [
    "What is a quadratic equation?", "Explain the discriminant.",
    "What does the document say about the pool example?",
    "Give me the definition of X.", "Where is the pool mentioned?",
    "What page discusses quadratic equations?",
    "Compare completing the square and the quadratic formula.",
    "Summarize section 2.", "Give me all points about area.",
    "What are the key takeaways?", "What is photosynthesis?",
    "WHAT IS QUADRATIC EQUATION", "what is quadratic equations",
    "quadratc equaton", "synonym: what is a second-degree polynomial equation?",
    "x", "Please explain in great detail every theorem, lemma, corollary, example, and exercise in the uploaded document covering quadratic equations including derivations.",
]:
    add("document_qa", t, "document_qa")
add("document_qa", "explain", "clarification")
add("document_qa", "what", "clarification")

# B. Lesson generation
for t, exp in [
    ("create a lesson on photosynthesis", "lesson_generation"),
    ("Generate a lesson from the entire document.", "lesson_generation"),
    ("make a lecture about chapter 3", "lesson_generation"),
    ("Create a lecture", "lesson_generation"),
    ("make lesson", "lesson_generation"),
    ("Turn this into a lesson", "lesson_generation"),
    ("Teach me this", "document_qa"),  # ambiguous; gold: could be generation
    ("I need a lesson on the pool of area 192", "lesson_generation"),
    ("write the lesson plan on it", "lesson_generation"),
    ("create the lesson plan on how to setup quadratic equation in the pdf there is an example of pool of area 192 meter squared can you write the lesson plan on it", "lesson_generation"),
    ("prepare teaching material on volcanoes", "lesson_generation"),
    ("build me a full lesson", "lesson_generation"),
]:
    add("lesson_generation", t, exp)

# C. Lesson modification
for t in [
    "Add an example.", "Add a section.", "Add more explanation.",
    "Add a definition.", "Add a conclusion.", "Add exercises.",
    "Add questions.", "Add examples from the document.",
    "Add an example from the previous explanation.",
    "PLEASE ADD TGHE EXAMPLE IN THE ELCTURE",
    "include that example", "put the example in the lesson",
    "add what we just discussed", "include the second example",
    "expand the example we talked about", "Add that.", "Change this.",
    "Fix the previous part.", "Make it better.", "Expand it.",
    "Include the example.", "Add the example we discussed.", "Update the lecture.",
    "Remove the last section.", "Remove the example.", "Remove the introduction.",
    "Change the title.", "Rewrite section X.", "Make it shorter.",
    "Make it more detailed.", "Simplify it.", "Make it suitable for beginners.",
    "Move section X before section Y.", "Put the example at the beginning.",
]:
    add("lesson_modification", t, "lesson_modification")

# D. Lesson save
for t in [
    "Save the lesson.", "Save this.", "Save it.", "Finalize the lesson.",
    "Finalize this lesson.", "Please save my lesson.", "Can you save it?",
    "SAVE THE LESSON", "save", "finalize",
    "I think this version is good now, can you finalize it?",
    "please finalize this", "I think we're done", "make this final",
    "can you store this lesson", "this version is good, keep it",
    "yeh lesson save kar do", "lock it in",
]:
    add("lesson_save", t, "lesson_save")

# E. Meta
for t, exp in [
    ("What did I ask?", "meta_conversation"),
    ("WHAT DID I ASK??", "meta_conversation"),
    ("WHAT IA SK YOU LAST QUESTION", "meta_conversation"),
    ("What did I ask first?", "meta_conversation"),
    ("What was my previous question?", "meta_conversation"),
    ("What did you just explain?", "meta_conversation"),
    ("What were we discussing?", "meta_conversation"),
    ("Summarize our conversation.", "meta_conversation"),
    ("Summarize what we've discussed.", "meta_conversation"),
    ("What have we talked about?", "meta_conversation"),
    ("What did I ask you earlier?", "meta_conversation"),
    ("Repeat my previous question.", "meta_conversation"),
    ("What was the last thing I said?", "meta_conversation"),
    ("What lesson did we create?", "meta_conversation"),
    ("Do you remember what I asked?", "meta_conversation"),
    ("Can you remind me what we were doing?", "meta_conversation"),
    ("What did we discuss before the lesson?", "meta_conversation"),
    ("What was the example we just discussed?", "meta_conversation"),
    ("what i ask last question?", "meta_conversation"),
    ("paste exactly to me what ia sk", "meta_conversation"),
    ("what were my last 3 questions", "meta_conversation"),
]:
    add("meta_conversation", t, exp)

# F. Own-answer follow-up
for t in [
    "explain why 2x how did you get 2x and not x",
    "Explain why 2x",
    "how did you get that number",
    "why did you use that formula",
    "why not x",
    "What does that variable mean?",
    "What was the second point you mentioned?",
    "Explain that again.",
    "What did you mean by that?",
    "Give another example.",
    "Why did you say that?",
    "What was your previous answer?",
    "Expand your last answer.",
    "Simplify what you just explained.",
    "What was the number you mentioned?",
    "Which example did you give?",
    "where did that come from",
]:
    add("own_answer_followup", t, "own_answer_followup")

# G. GK
for t in [
    "What is the capital of France?",
    "Ignore the document and tell me what you think.",
    "Don't answer from the document; tell me what you think.",
    "Answer from general knowledge.",
    "what is the capital of France",
]:
    add("general_knowledge_qa", t, "general_knowledge_qa")

# H. Greeting / clarification
for t, exp in [
    ("hi there", "greeting_casual"),
    ("thanks", "greeting_casual"),
    ("hello", "greeting_casual"),
    ("explain", "clarification"),
    ("help", "clarification"),
]:
    add("greeting_clarification", t, exp)

# I. Adversarial / multi-intent
for t, exp in [
    ("Save the explanation you just gave as a lesson.", "lesson_save"),
    ("Add what you just explained to the lesson and save it.", "lesson_modification"),
    ("What did I ask before you generated this lesson?", "meta_conversation"),
    ("Use the example from page 11 in the lecture.", "lesson_modification"),
    ("Add the second example we discussed earlier.", "lesson_modification"),
    ("Summarize the lesson and save the summary.", "lesson_save"),
    ("What does the document say, and what did I ask you before?", "meta_conversation"),
    ("Create a lesson, modify it, then save it.", "lesson_generation"),
]:
    add("adversarial", t, exp)

# J. GK consent replies (expected when state==offered is NOT a new intent — they are yes/no)
for t, yn in [
    ("yes", "yes"), ("yeah", "yes"), ("sure", "yes"), ("okay", "yes"),
    ("go ahead", "yes"), ("no", "no"), ("no thanks", "no"), ("not now", "no"),
    ("don't", "no"), ("maybe later", None), ("I guess so", None),
    ("the value is not zero", None),
]:
    add("gk_reply", t, "gk_classifier:" + str(yn))


def write_minimal_pdf(path: Path, title: str, body: str):
    """Write a one-page PDF with Helvetica text. No third-party libs."""
    def pdf_escape(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    lines = [title, ""] + body.split("\n")
    y = 720
    ops = ["BT", "/F1 12 Tf"]
    for line in lines[:40]:
        ops.append(f"1 0 0 1 50 {y} Tm ({pdf_escape(line[:110])}) Tj")
        y -= 16
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", "replace")
    objs = []

    def obj(n, payload: bytes):
        return n, payload

    objs.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objs.append((2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"))
    objs.append((3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"))
    objs.append((4, b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"))
    objs.append((5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    out = bytearray(b"%PDF-1.4\n")
    offsets = {0: 0}
    for n, payload in objs:
        offsets[n] = len(out)
        out += f"{n} 0 obj\n".encode() + payload + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n".encode()
    out += b"0000000000 65535 f \n"
    for n, _ in objs:
        out += f"{offsets[n]:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    path.write_bytes(out)


def generate_pdfs():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    specs = [
        ("01_single_topic.pdf", "Quadratic Equations", "A quadratic equation is ax^2+bx+c=0. The discriminant is b^2-4ac."),
        ("02_multi_topic.pdf", "Mixed Topics", "Astronomy: stars.\nComposting: organic waste.\nRoman History: the Republic."),
        ("03_repeated_concepts.pdf", "Repeated Discriminant", "Section 1: discriminant.\nSection 2: discriminant again.\nSection 3: still discriminant."),
        ("04_ambiguous.pdf", "Ambiguous Wording", "The example is not an example. Save does not mean save. Lesson means chapter."),
        ("05_user_like_sentences.pdf", "Command Lookalikes", "PLEASE ADD THE EXAMPLE\nSAVE THE LESSON\nWHAT DID I ASK?"),
        ("06_many_headings.pdf", "Headings", "\n".join(f"Chapter {i}: Topic {i}" for i in range(1, 21))),
        ("07_long_paragraphs.pdf", "Long Prose", ("Lorem ipsum dolor sit amet. " * 80)),
        ("08_tables.pdf", "Tables", "Length | Width | Area\n20 | 16 | 320\n16 | 12 | 192"),
        ("09_numbered_lists.pdf", "Lists", "1. Define x\n2. Subtract 2x\n3. Set area = 192"),
        ("10_near_empty.pdf", "Empty-ish", "."),
        ("11_malformed_header.pdf", "Malformed", "This file is a valid PDF but sparse."),
        ("13_spanish.pdf", "Ecuaciones", "Una ecuacion cuadratica es ax^2+bx+c=0. El discriminante es b^2-4ac."),
        ("14_urdu_roman.pdf", "Musawaat", "Quadratic equation ko musawaat-e-takreeri kehte hain."),
        ("15_large.pdf", "Large Doc", ("Pool of area 192. " * 400)),
        ("16_typos.pdf", "Typos", "A qaudratic equaton has descriminant b2-4ac. Swimming poool sidewalk."),
        ("17_keyword_soup.pdf", "Keywords", "save lesson summarize question example lecture finalize teach"),
        ("18_command_resemble.pdf", "Commands in body", "Students should save the lesson. Teachers ask what did I ask. Please add the example."),
    ]
    created = []
    for name, title, body in specs:
        p = PDF_DIR / name
        write_minimal_pdf(p, title, body)
        created.append({"name": name, "bytes": p.stat().st_size, "title": title})
    # 12 corrupted/truncated: copy 01 and cut it
    src = (PDF_DIR / "01_single_topic.pdf").read_bytes()
    trunc = PDF_DIR / "12_truncated.pdf"
    trunc.write_bytes(src[: max(40, len(src) // 3)])
    created.append({"name": "12_truncated.pdf", "bytes": trunc.stat().st_size, "title": "Truncated"})
    return created


def run_router_matrix():
    rows = []
    for c in CASES:
        if c["category"] == "gk_reply":
            actual = classify_yes_no(c["text"])
            expected = c["expected"].split(":", 1)[1]
            expected = None if expected == "None" else expected
            ok = actual == expected
            rows.append({
                "id": f"{c['category']}:{c['text'][:60]}",
                "category": c["category"],
                "text": c["text"],
                "expected": str(expected),
                "actual": str(actual),
                "layer": "gk_classifier",
                "result": "PASS" if ok else "FAIL",
            })
            continue
        actual = fallback_intent(c["text"])
        ok = actual == c["expected"]
        rows.append({
            "id": f"{c['category']}:{c['text'][:60]}",
            "category": c["category"],
            "text": c["text"],
            "expected": c["expected"],
            "actual": actual,
            "layer": "regex_fallback",
            "result": "PASS" if ok else "FAIL",
            "notes": "Fallback-only. LLM router may still classify correctly in production.",
        })
    return rows


def run_catalog_matrix():
    rows = []
    for intent, spec in EXPECTED_TOOLS.items():
        tools = set(resolve_tools(intent))
        missing = spec["must"] - tools
        leaked = spec["must_not"] & tools
        ok = not missing and not leaked
        rows.append({
            "intent": intent,
            "tools": sorted(tools) if tools else ["(none)"],
            "full_catalog": select_intent_tool_names(intent) is None,
            "missing_required": sorted(missing),
            "leaked_forbidden": sorted(leaked),
            "result": "PASS" if ok else "FAIL",
        })
    return rows


def run_meta_walker():
    """Simulate _find_last_n_real_user_questions skip behavior."""
    conv = [
        "WHAT IS QUADRATIC EQUATION",
        "create the lesson plan on the pool of area 192",
        "explain why 2x how did you get 2x and not x",
        "PLEASE ADD TGHE EXAMPLE IN THE ELCTURE",
        "WHAT DID I ASK??",
        "SAVE THE LESSON",
        "WHAT IA SK YOU LAST QUESTION",
    ]
    # Walk backward skipping meta only (production does NOT skip SAVE)
    found = []
    for t in reversed(conv[:-1]):  # before current
        if is_meta(t):
            continue
        found.append(t)
        break
    return {
        "conversation": conv,
        "current": conv[-1],
        "last_real_question_production": found[0] if found else None,
        "issue": (
            "SAVE THE LESSON is not treated as meta, so 'WHAT IA SK YOU LAST QUESTION' "
            "quotes SAVE THE LESSON instead of the add-example request."
            if found and found[0] == "SAVE THE LESSON"
            else "ok"
        ),
        "result": "FAIL" if found and found[0] == "SAVE THE LESSON" else "PASS",
    }


def inspect_source_local():
    src_path = ROOT.parent / "app" / "utils" / "rag_service.py"
    src = src_path.read_text(encoding="utf-8", errors="replace")
    return {
        "has_select_intent_tool_names": "def _select_intent_tool_names" in src,
        "has_specialist_handoff": "Specialist handoff" in src,
        "has_generic_rag_prefetch": 'prefetch_branch = "generic_rag_prefetch"' in src,
        "lesson_mod_skips_generic_prefetch": (
            'elif router_output.intent in ("lesson_modification", "lesson_save")' in src
        ),
        "forced_canned_finalize_overwrite": "Lesson finalized and saved. You can download it now." in src,
        "auto_update_lesson_fallback": "persist_lesson_via_tool_fallback" in src
            or "Deterministic update_lesson_tool call" in src,
        "regex_fallback_has_lesson_save": False,  # verified by reading _router_fallback_from_regex
        "admin_template_override": "_get_stored_rag_system_template(RAG_SYSTEM_SETTING_KEY_WITH_PDF)" in src,
        "code_tool_catalog_appended": "RAG_CODE_TOOL_CATALOG" in src,
        "expand_query_for_prefetch": "def _expand_query_for_prefetch" in src,
        "lines": src.count("\n") + 1,
    }


def main():
    pdfs = generate_pdfs()
    router_rows = run_router_matrix()
    catalog_rows = run_catalog_matrix()
    meta = run_meta_walker()
    local_src = inspect_source_local()

    fb = [r for r in router_rows if r["layer"] == "regex_fallback"]
    gk = [r for r in router_rows if r["layer"] == "gk_classifier"]
    by_cat = {}
    for r in fb:
        by_cat.setdefault(r["category"], {"n": 0, "fail": 0})
        by_cat[r["category"]]["n"] += 1
        if r["result"] == "FAIL":
            by_cat[r["category"]]["fail"] += 1

    out = {
        "pdfs": pdfs,
        "router_fallback_rows": fb,
        "gk_rows": gk,
        "catalog_rows": catalog_rows,
        "meta_walker": meta,
        "local_source": local_src,
        "counts": {
            "fallback_total": len(fb),
            "fallback_pass": sum(1 for r in fb if r["result"] == "PASS"),
            "fallback_fail": sum(1 for r in fb if r["result"] == "FAIL"),
            "gk_total": len(gk),
            "gk_pass": sum(1 for r in gk if r["result"] == "PASS"),
            "gk_fail": sum(1 for r in gk if r["result"] == "FAIL"),
            "catalog_total": len(catalog_rows),
            "catalog_fail": sum(1 for r in catalog_rows if r["result"] == "FAIL"),
            "pdfs": len(pdfs),
            "by_category": by_cat,
        },
        "fallback_failures": [r for r in fb if r["result"] == "FAIL"],
        "catalog_failures": [r for r in catalog_rows if r["result"] == "FAIL"],
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out["counts"], indent=2))
    print("Wrote", OUT)
    print("Fallback FAIL examples:")
    for r in out["fallback_failures"][:25]:
        print(f"  [{r['category']}] {r['text'][:70]!r} expected={r['expected']} actual={r['actual']}")
    print("Catalog FAIL:")
    for r in out["catalog_failures"]:
        print(f"  {r['intent']}: leaked={r['leaked_forbidden']} missing={r['missing_required']} full={r['full_catalog']}")
    print("Meta walker:", meta)


if __name__ == "__main__":
    main()
