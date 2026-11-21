"""DIL weekly feedback 17-11-2026 - regression tests.

1. AI Guided Practice on a question-bank PDF served 8/10 AI-made questions although
   the page said the questions come from the uploaded bank.
3. Tutor replies stopped mid-sentence ("try this tiny version: if (x"), showed raw
   \\( / $ delimiters, and said "the PDF".
4. Urdu: "explain it in urdu" -> "Sorry, I could not finish"; Urdu replies were
   typeset as one math span (no spaces, sideways scroll); an Urdu reply revealed
   the answer factor.
6. Remember this device / shared-computer password autofill.
"""
import json
import pathlib
import re

import pytest

from test_ai_guided_practice import STUDENT_A, _bank, _chunks, _sub, env  # noqa: F401 - fixture
from app.services.lms import ai_guided_practice_service as svc
from app.services.lms import tutor_service as T
from app.services.lms import tutor_text as TT

ROOT = pathlib.Path(__file__).resolve().parents[1]

# A question bank where one label has a single printed item (the live
# "Algebraic Expressions: Rational Expressions" case) and a related label has two.
QBANK_RAT = {
    1: "Q1. Which of these is a rational expression?\n"
       "Domain: Algebra | Topic: Algebraic Expressions: Rational Expressions | Cognitive Level: Knowledge\n"
       "A. P(x)/Q(x)\nB. x+1\nC. sqrt x\nD. 2\n"
       "Q2. Reduce (x^2-9)/(x^2+5x+6) to lowest terms.\n"
       "Domain: Algebra | Topic: Simplifying Rational Expressions | Cognitive Level: Application\n"
       "A. (x-3)/(x+2)\nB. x-3\nC. x+2\nD. 1\n",
    2: "Q3. Simplify (x^2-9)/(x^2-5x+6).\n"
       "Domain: Algebra | Topic: Simplifying Rational Expressions | Cognitive Level: Application\n"
       "A. (x+3)/(x-2)\nB. x+3\nC. x-2\nD. 1\n"
       "Q4. Rationalize 1/sqrt 2.\n"
       "Domain: Algebra | Topic: Surds and Rationalization | Cognitive Level: Application\n"
       "A. sqrt2/2\nB. 2\nC. 1\nD. sqrt 2\n"
       "Q5. Rationalize 1/sqrt 3.\n"
       "Domain: Algebra | Topic: Surds and Rationalization | Cognitive Level: Application\n"
       "A. sqrt3/3\nB. 3\nC. 1\nD. sqrt 3\n",
}


# -- 1. AI Guided Practice: question-bank topics serve only the printed bank ----------


def test_bank_topic_serves_only_printed_questions_and_never_generates(env):
    from test_ai_guided_practice import QBANK

    env.pdfs["thread-v1"] = QBANK
    _bank(env, {4: ("approved", "Factorise x^2-9 (bank)", 0), 5: ("approved", "Factorise x^2-4 (bank)", 0)})
    sub = _sub(svc.dashboard(STUDENT_A), "Factorization")
    st = svc.start_session(STUDENT_A, sub["subtopic_id"])
    texts = sorted(i["question_text"] for i in st["items"])
    assert texts == ["Factorise x^2-4 (bank)", "Factorise x^2-9 (bank)"]  # short session, no AI questions
    assert env.gen.calls == []                                            # LLM generation never ran
    assert env.topups == []                                               # no background top-up either
    assert st["question_source"] == "bank"
    assert svc.ensure_pool(sub["subtopic_id"], 50)["generated"] == 0


def test_bank_topic_hides_previously_generated_questions(env):
    from test_ai_guided_practice import QBANK
    from app.models.lms_models import AIGPQuestion, AIGPTopic

    env.pdfs["thread-v1"] = QBANK
    sub_id = _sub(svc.dashboard(STUDENT_A), "Factorization")["subtopic_id"]
    sub = env.db.query(AIGPTopic).filter_by(id=sub_id).one()
    # AI-made questions already in the pool from before this fix (live: 50 vs 1 bank)
    for k in range(12):
        env.db.add(AIGPQuestion(version_id=sub.version_id, topic_id=sub_id, question_text=f"AI made {k}",
                                options_json=json.dumps([{"label": c, "text": f"o{c}"} for c in "ABCD"]),
                                correct_option_index=0, dedupe_key=f"ai{k}"))
    env.db.commit()
    _bank(env, {4: ("approved", "Factorise x^2-9 (bank)", 0)})
    st = svc.start_session(STUDENT_A, sub_id)
    assert [i["question_text"] for i in st["items"]] == ["Factorise x^2-9 (bank)"]
    assert st["question_source"] == "bank"
    assert env.gen.calls == []


def test_bank_topic_without_approved_questions_says_so_instead_of_generating(env):
    from test_ai_guided_practice import QBANK

    env.pdfs["thread-v1"] = QBANK
    _bank(env, {4: ("needs_review", "draft", 0), 5: ("auto_verified", "draft 2", 0)})
    sub = _sub(svc.dashboard(STUDENT_A), "Factorization")
    st = svc.start_session(STUDENT_A, sub["subtopic_id"])
    assert st["session"] is None and st["reason"] == "no_bank_questions"
    assert env.gen.calls == []


def test_tiny_label_gets_only_its_own_printed_items_never_another_topic(env):
    """User decision 2026-11-22: a label with 1 printed question gives a 1-question
    session - no items from similar labels, no AI-made questions."""
    env.pdfs["thread-v1"] = QBANK_RAT
    _bank(env, {1: ("approved", "Rational expression?", 0), 2: ("approved", "Reduce to lowest terms", 0),
                3: ("approved", "Simplify fraction", 0), 4: ("approved", "Rationalize 1/sqrt2", 0),
                5: ("approved", "Rationalize 1/sqrt3", 0)})
    dash = svc.dashboard(STUDENT_A)
    st = svc.start_session(STUDENT_A, _sub(dash, "Algebraic Expressions: Rational Expressions")["subtopic_id"])
    assert [i["question_text"] for i in st["items"]] == ["Rational expression?"]
    st = svc.start_session(STUDENT_A, _sub(dash, "Simplifying Rational Expressions")["subtopic_id"])
    assert sorted(i["question_text"] for i in st["items"]) == ["Reduce to lowest terms", "Simplify fraction"]
    assert env.gen.calls == []


def test_repeat_session_on_tiny_label_reuses_its_own_question(env):
    env.pdfs["thread-v1"] = QBANK_RAT
    _bank(env, {1: ("approved", "Rational expression?", 0), 2: ("approved", "Reduce to lowest terms", 0)})
    sub_id = _sub(svc.dashboard(STUDENT_A), "Algebraic Expressions: Rational Expressions")["subtopic_id"]
    first = svc.start_session(STUDENT_A, sub_id)
    svc.complete_session(STUDENT_A, first["session_id"])
    second = svc.start_session(STUDENT_A, sub_id)
    assert [i["question_text"] for i in second["items"]] == ["Rational expression?"]
    assert env.gen.calls == []


def test_session_page_names_the_question_bank_honestly():
    js = (ROOT / "static/student/js/student-ai-guided-practice.js").read_text(encoding="utf-8")
    assert "Questions generated only from your teacher" not in js
    assert "All questions are taken from your teacher\\'s question bank" in js
    assert "no_bank_questions" in js


# -- 3 + 4. Tutor text ----------------------------------------------------------------


@pytest.mark.parametrize("msg,hist,lang", [
    ("explain it in urdu", [], "urdu"),
    ("Explain this in simpler words", [{"role": "user", "content": "explain it in urdu"}], "urdu"),
    ("switch back to english", [{"role": "user", "content": "explain it in urdu"}], "english"),
    ("Give me a hint", [{"role": "user", "content": "switch back to english"},
                        {"role": "user", "content": "explain it in urdu"}][::-1], "english"),
    ("یہ سمجھ نہیں آیا", [], "urdu"),
    ("mujhe samajh nahi aya", [], "roman_urdu"),
    ("roman urdu mein samjhao", [], "roman_urdu"),
    ("Why is my answer correct?", [], "english"),
])
def test_reply_language_follows_and_remembers_the_student_choice(msg, hist, lang):
    assert TT.reply_language(msg, hist) == lang


def test_urdu_rule_reaches_the_tutor_context():
    ctx = T.build_deficiency_context(current_question={"question_text": "q"}, message="Give a hint",
                                     history=[{"role": "user", "content": "explain it in urdu"}])
    assert "Reply ONLY in clear, simple Urdu" in ctx
    assert "MATH FORMAT" in ctx and "never mention" in ctx


@pytest.mark.parametrize("raw,expected", [
    # nested delimiters used as brackets (screenshot 4)
    (r"Think of \(\(x - \frac{1}{x}\)^2\) as a square.", r"Think of \((x - \frac{1}{x})^2\) as a square."),
    # delimiters used as brackets with the power outside
    (r"Think of \(x - \frac{1}{x}\)^2 as a square.", r"Think of \((x - \frac{1}{x})^2\) as a square."),
    # $...$ -> \(...\)
    (r"So $x^{2/3}$ is a cube root.", r"So \(x^{2/3}\) is a cube root."),
    # prose inside math is unwrapped (screenshot 5: "coefficientof$x^2$")
    # (its maths tokens are wrapped again on their own)
    (r"Look for \(coefficient of x^2 \times constant term\) = 6.", r"Look for coefficient of \(x^2 \times\) constant term = 6."),
    # unclosed span at the end is closed
    (r"Now write \(a^3 + b^3", r"Now write \(a^3 + b^3\)"),
    # stray closer
    (r"so 5 + 1\) = 6", "so 5 + 1) = 6"),
    # clean replies stay as they are
    (r"First step: \(5^{2}\times5^{4}=5^{6}\).", r"First step: \(5^{2}\times5^{4}=5^{6}\)."),
    ("\\[\n\\frac{x^{-3}}{x^{2}} = x^{-5}\n\\]", "\\[\n\\frac{x^{-3}}{x^{2}} = x^{-5}\n\\]"),
])
def test_math_delimiters_are_made_well_formed(raw, expected):
    assert TT.fix_math_delimiters(raw) == expected
    assert TT.fix_math_delimiters(expected) == expected  # idempotent


def test_urdu_words_never_end_up_inside_math():
    out = TT.tidy_reply(r"\(a^3 + b^3 کا مطلب ہے\)")
    assert out == r"\(a^3 + b^3\) کا مطلب ہے"  # only the maths is inside the delimiters


def test_reply_never_mentions_the_pdf_or_json_residue():
    out = TT.tidy_reply('First step: Look at the definition in the PDF: it says so. Want to see how this matches '
                        'the other examples in the PDF?"}')
    assert "PDF" not in out and not out.endswith('"}')
    assert "your book" in out


def test_truncation_helpers():
    assert TT.unbalanced_brackets("For practice, try this tiny version: if (x")
    assert not TT.unbalanced_brackets(r"Use \(x\) and (a + b).")
    t = "Think of a factor as a perfect fit. It divides exactly. For practice, try this tiny version: if (x"
    assert TT.trim_to_last_sentence(t) == "Think of a factor as a perfect fit. It divides exactly."
    ur = "یہ ایک آسان مثال ہے جس سے بات سمجھ آئے گی۔ اگلا قدم یہ ہے کہ (a"
    assert TT.trim_to_last_sentence(ur) == "یہ ایک آسان مثال ہے جس سے بات سمجھ آئے گی۔"
    assert TT.trim_to_last_sentence("if (x") is None


URDU_LEAK = ("اس طرح کا ٹرم (a² - ab + b²) بنتا ہے۔ یہ ہی وہ قدم ہے جو a³ + b³ کو فیکٹرائز کرنے میں مدد دیتا ہے۔")
OPTS = [{"label": "A", "text": "(a+b)^3 - ab(a+b)"}, {"label": "B", "text": "(a+b)(a^2-ab+b^2)"},
        {"label": "C", "text": "(a+b)(a^2+ab+b^2)"}, {"label": "D", "text": "(a-b)(a^2+ab+b^2)"}]


def test_urdu_reply_that_builds_the_correct_factor_is_a_leak():
    assert TT.reveals_option_math(URDU_LEAK, OPTS, 1, "Which identity gives a^3 + b^3?")
    # (a + b) is shared with wrong options: naming it is a fair hint
    assert not TT.reveals_option_math("پہلے (a + b) کو باہر نکالیں۔", OPTS, 1, "a^3 + b^3?")
    assert not TT.reveals_option_math("Think about pulling out (a+b).", OPTS, 1, "a^3+b^3")


def test_learning_chat_guard_uses_the_math_check():
    from app.services.lms.deficiency_chat_service import _reveals_current_mcq_answer

    q = {"question_text": "a^3 + b^3 = ?", "options": OPTS, "correct_option_index": 1}
    assert _reveals_current_mcq_answer(URDU_LEAK, q)
    assert not _reveals_current_mcq_answer("Try taking (a+b) out first. What is left?", q)


class _Reply:
    def __init__(self, content):
        self.content = content
        self.tool_calls = []


class _LLM:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.max_tokens = None

    def bind_tools(self, _tools):
        return self

    def invoke(self, msgs):
        self.calls.append(msgs)
        return _Reply(self.replies.pop(0) if self.replies else "")


def _run(monkeypatch, replies, message="Give me a hint", context="", solved=False):
    llm = _LLM(replies)
    seen = {}

    def fake_create_llm(**kw):
        seen.update(kw)
        return llm

    monkeypatch.setattr(T, "create_llm", fake_create_llm)
    out = T.tutor_chat(message, api_key="k", mode="deficiency", context=context, history=[], solved=solved)
    return out, llm, seen


def test_token_budget_leaves_room_for_reasoning_and_urdu(monkeypatch):
    _, _, seen = _run(monkeypatch, ["Think about what (x + 3) means."])
    assert seen["max_tokens"] >= 2500  # was 260 - reasoning tokens ate the reply
    _, _, seen = _run(monkeypatch, ["ٹھیک ہے۔"], message="اردو میں سمجھائیں")
    assert seen["max_tokens"] >= 3500


def test_cut_off_reply_is_retried_then_trimmed_not_shown_half(monkeypatch):
    cut = ("Think of a factor like a perfect fit. Plugging in -3 must give zero. "
           "For practice, try this tiny version: if (x")
    out, llm, _ = _run(monkeypatch, [cut, cut])
    assert len(llm.calls) == 2                      # one rewrite was requested
    assert out == "Think of a factor like a perfect fit. Plugging in -3 must give zero."
    out, _, _ = _run(monkeypatch, [cut, "Think of a factor as a perfect fit. What is f(-3)?"])
    assert out == "Think of a factor as a perfect fit. What is f(-3)?"


def test_tutor_output_is_tidied(monkeypatch):
    out, _, _ = _run(monkeypatch, [r"Great job! Look at the definition in the PDF: $x^2$ is a square."], solved=True)
    assert "PDF" not in out and r"\(x^2\)" in out


# -- 4. Urdu rendering in the browser pipeline (static checks; Playwright covers the DOM) --


def test_client_never_wraps_urdu_text_as_math():
    js = (ROOT / "static/lms/lms-core.js").read_text(encoding="utf-8")
    assert "lmsHasArabicScript(s)" in js and "lmsUnwrapArabicMath" in js
    assert 'dir="rtl" lang="ur"' in js
    css = (ROOT / "static/lms/lms-ui.css").read_text(encoding="utf-8")
    assert '.lms-math-content[dir="rtl"]' in css


# -- 6. Remember this device -----------------------------------------------------------


def test_login_page_scopes_saved_password_to_its_email():
    html = (ROOT / "templates/login/login.html").read_text(encoding="utf-8")
    assert "body.set('remember'" in html
    assert "PasswordCredential" in html
    assert "dropForeignPassword" in html
    assert 'autocomplete="username"' in html and 'autocomplete="current-password"' in html
    assert "localStorage.setItem('password" not in html and "setItem(REMEMBER_KEY, remember" in html


def test_leak_guard_matches_pdf_maths_italic_options():
    """Live E2E 2026-11-22: Urdu reply wrote the full answer for a bank option stored with
    maths-italic letters and no '^' - the guard has to see they are the same."""
    opts = [{"label": "A", "text": "(𝑎−𝑏)(𝑎2 −𝑎𝑏+ 𝑏2)"}, {"label": "B", "text": "(𝑎−𝑏)(𝑎2 + 𝑎𝑏+ 𝑏2)"},
            {"label": "C", "text": "(𝑎+ 𝑏)(𝑎2 −𝑎𝑏−𝑏2)"}, {"label": "D", "text": "(𝑎−𝑏)(𝑎2 + 2𝑎𝑏+ 𝑏2)"}]
    reply = r"فرقِ مکعب کا فارمولا یہ ہے: \(a^3-b^3 = (a-b)(a^2+ab+b^2)\)۔"
    assert TT.reveals_option_math(reply, opts, 1, "Which formula correctly factorizes 𝑎3 −𝑏3 ?")
    hint = r"پہلے \((a-b)\) کو ایک دو رُکنی سے ضرب دیں۔"
    assert not TT.reveals_option_math(hint, opts, 1, "Which formula correctly factorizes 𝑎3 −𝑏3 ?")


def test_leak_guard_sees_through_bigl_bigr_display_maths():
    r"""Live E2E 2026-11-22: '\bigl(a^{2}-ab+b^{2}\bigr)' in a display block was missed."""
    opts = [{"label": "A", "text": "(𝑎+ 𝑏)(𝑎2 + 𝑎𝑏+ 𝑏2)"}, {"label": "B", "text": "(𝑎+ 𝑏)(𝑎2 −𝑎𝑏+ 𝑏2)"},
            {"label": "C", "text": "(𝑎−𝑏)(𝑎2 + 𝑎𝑏+ 𝑏2)"}, {"label": "D", "text": "(𝑎+ 𝑏)3"}]
    reply = "اس کا معیاری فارمولا یہ ہے\n" + r"\[" + "\n" + r"a^3+b^3=(a+b)\bigl(a^{2}-ab+b^{2}\bigr)" + "\n" + r"\]"
    assert TT.reveals_option_math(reply, opts, 1, "Which identity factorizes 𝑎3 + 𝑏3 ?")


def test_answer_guard_reply_speaks_the_students_language_and_never_says_pdf():
    from app.services.lms.deficiency_chat_service import _ANSWER_GUARD_REPLY

    assert TT.answer_guard_reply("Give a hint", [{"role": "user", "content": "explain it in urdu"}]) == \
        TT.ANSWER_GUARD_REPLIES["urdu"]
    assert TT.answer_guard_reply("Give a hint", []) == _ANSWER_GUARD_REPLY
    assert all("PDF" not in r for r in TT.ANSWER_GUARD_REPLIES.values()) and "PDF" not in _ANSWER_GUARD_REPLY


def test_spacing_commands_do_not_leak_as_text_when_urdu_is_unwrapped():
    out = TT.tidy_reply(r"دیے گئے ہیں: \(a+b = 7 \qquadاور\qquad a-b = 3\) ہمیں")
    assert "qquad" not in out and "اور" in out and "a+b = 7" in out


NUM_OPTS = [{"label": "A", "text": "12"}, {"label": "B", "text": "64"}, {"label": "C", "text": "81"}, {"label": "D", "text": "27"}]
NUM_STEM = "Solve for 𝑥: log3(𝑥) = 4"


@pytest.mark.parametrize("reply", [
    r"So \(3^{4} = 81\), and that is x.",
    "x is 81.",
    "یعنی x برابر 81",
    r"تو \(x\) کی قیمت \(81\) ہے۔",
])
def test_numeric_key_stated_as_result_is_a_leak(reply):
    assert TT.reveals_option_math(reply, NUM_OPTS, 2, NUM_STEM)


@pytest.mark.parametrize("reply", [
    r"Rewrite it as \(3^{4} = x\) and work out \(3^4\).",            # method only
    "Multiply 3 by itself 4 times, then compare with the options.",
    "Is 64 the result? Check: 4^3 = 64, but the base here is 3.",   # wrong option discussed
    r"\(\log_{3}(x)=4\) کو \(3^{4}=x\) لکھیں۔",
])
def test_numeric_hints_are_not_leaks(reply):
    assert not TT.reveals_option_math(reply, NUM_OPTS, 2, NUM_STEM)


@pytest.mark.parametrize("text,bare", [
    ("I’m sorry, but I can’t provide that.", True),
    ("I'm sorry, but I can't share that.", True),
    (r"I can't give the answer directly, but here's a hint: rewrite it as \(3^4 = x\).", False),
    ("Sorry! Think about what (a + b) times itself gives?", False),
    ("معذرت، میں یہ نہیں بتا سکتا۔", True),
    ("Great job! Yes - B is right.", False),
])
def test_bare_refusal_detection(text, bare):
    assert TT.is_bare_refusal(text) is bare


def test_bare_refusal_is_rewritten_into_help(monkeypatch):
    out, llm, _ = _run(monkeypatch, ["I’m sorry, but I can’t provide that.",
                                     r"Try writing \(\log_3 x = 4\) as a power of 3. What do you get?"],
                       message="Please explain this in Urdu")
    assert len(llm.calls) == 2 and "power of 3" in out


def test_repeated_bare_refusal_becomes_a_nudge_in_the_students_language(monkeypatch):
    out, _, _ = _run(monkeypatch, ["I’m sorry, but I can’t provide that.", "I’m sorry, but I can’t."],
                     message="Please explain this in Urdu")
    assert out == TT.ANSWER_GUARD_REPLIES["urdu"]
