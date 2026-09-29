# Diagnostic E2E Deep Test Report

**Server:** https://209.23.10.34  
**Run ID:** 1788292063  
**Assessment ID:** 32

**Result:** 47 pass / 3 fail / 50 total

- ✅ **Admin** — Upload Q&A + 3 targets: HTTP 201
- ✅ **Admin** — Upload progress complete: Diagnostic processing complete.
- ✅ **Admin** — Publish diagnostic: id=32
- ✅ **Admin** — Target PDFs count = 3: count=3
- ✅ **Admin** — Single published diagnostic: published=1
- ✅ **Setup** — Create student 1: e2e_student_1788292063_1@iqbalai.com
- ✅ **Setup** — Create student 2: e2e_student_1788292063_2@iqbalai.com
- ✅ **Setup** — Create student 3: e2e_student_1788292063_3@iqbalai.com
- ✅ **Student-1** — See published diagnostic: id=32
- ✅ **Student-1** — Start diagnostic + timer: {"attempt_id": 21, "remaining_seconds": 296, "expires_at": "2026-09-01T19:53:22.969016Z"}
- ✅ **Student-1** — Load questions: count=8
- ✅ **Student-1** — Submit diagnostic: score=25.0% weak=3
- ✅ **Student-1** — Retake blocked: code=400
- ✅ **Student-2** — See published diagnostic: id=32
- ✅ **Student-2** — Start diagnostic + timer: {"attempt_id": 22, "remaining_seconds": 296, "expires_at": "2026-09-01T19:53:29.703658Z"}
- ✅ **Student-2** — Load questions: count=8
- ✅ **Student-2** — Submit diagnostic: score=37.5% weak=4
- ✅ **Student-2** — Retake blocked: code=400
- ✅ **Student-3** — See published diagnostic: id=32
- ✅ **Student-3** — Start diagnostic + timer: {"attempt_id": 23, "remaining_seconds": 296, "expires_at": "2026-09-01T19:53:36.554607Z"}
- ✅ **Student-3** — Load questions: count=8
- ✅ **Student-3** — Submit diagnostic: score=37.5% weak=4
- ✅ **Student-3** — Retake blocked: code=400
- ❌ **Student-1** — Learning path generated: steps=0
- ✅ **Student-1** — Force-generate learning path: steps=1
- ✅ **Student-1** — Has current step: Quiz #0
- ✅ **Student-1** — Mark first path step complete: item_id=9
- ✅ **Student-1** — Student progress API: keys=['learning_path', 'overall_progress', 'topics']
- ✅ **Student-1** — Start Learning Chat: session=15
- ✅ **Student-1** — Answer practice question: {'data': {'completed': False, 'correct_count': 1, 'current_index': 1, 'current_question': {'answered': False, 'correct': None, 'options': [{'label': '
- ✅ **Student-1** — Tutor explain (PDF-grounded): Sure! Let’s look at the key idea from the target PDF on linear equations.

### What the PDF tells us
A linear equation i
- ❌ **Student-2** — Learning path generated: steps=0
- ✅ **Student-2** — Force-generate learning path: steps=1
- ✅ **Student-2** — Has current step: Quiz #0
- ✅ **Student-2** — Mark first path step complete: item_id=10
- ✅ **Student-2** — Student progress API: keys=['learning_path', 'overall_progress', 'topics']
- ✅ **Student-2** — Start Learning Chat: session=16
- ✅ **Student-2** — Answer practice question: {'data': {'completed': False, 'correct_count': 1, 'current_index': 1, 'current_question': {'answered': False, 'correct': None, 'options': [{'label': '
- ✅ **Student-2** — Tutor explain (PDF-grounded): Sure! Let’s look at the idea behind solving a linear equation like the one you have.

**What the PDF tells us**

A linea
- ❌ **Student-3** — Learning path generated: steps=0
- ✅ **Student-3** — Force-generate learning path: steps=1
- ✅ **Student-3** — Has current step: Quiz #0
- ✅ **Student-3** — Mark first path step complete: item_id=11
- ✅ **Student-3** — Student progress API: keys=['learning_path', 'overall_progress', 'topics']
- ✅ **Student-3** — Start Learning Chat: session=17
- ✅ **Student-3** — Answer practice question: {'data': {'completed': False, 'correct_count': 0, 'current_index': 1, 'current_question': {'answered': False, 'correct': None, 'options': [{'label': '
- ✅ **Student-3** — Tutor explain (PDF-grounded): Sure! Let’s look at the idea of solving a linear equation like the one in your PDF excerpt:

**Linear equations – ax + b
- ✅ **Browser-e2e_student_1788292063_1** — Student dashboard loads: 
- ✅ **Browser-e2e_student_1788292063_1** — Learning Path section visible: 
- ✅ **Browser-e2e_student_1788292063_1** — Learning Chat modal opens: 