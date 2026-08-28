# IqbalAI LMS — Master Roadmap (Final)

> Task-level prompts: see phase folders. Each task has a `.md` file with a **Cursor Implementation Prompt**.

## Locked Requirements

- **Diagnostic:** Manual Question Bank OR PDF + topic selection + AI questions
- **Assignment:** Quiz only (one quiz per assignment)
- **Quiz creation:** PDF Q&A Auto (primary) → open-ended Q+A in PDF → 4-option MCQ
- **PDF parsing:** Pydantic + LangChain structured output (format-flexible AI)
- **Confidence:** &lt;0.60 block publish | 0.60–0.84 review | ≥0.85 auto

## MVP Flow

**Teacher:** Upload Q&A PDF → AI extracts Q↔A → MCQ (4 options) → preview/edit → assign to class  
**Student:** Join class → diagnostic → take quiz → see progress  

## Implementation Order

1. **F-001** + **A-325** (parallel)
2. F-002 → F-008, F-009, F-010, F-011
3. A-326 → A-329 (PDF→MCQ pipeline)
4. TE-601 → TE-613 (classes + assignments)
5. A-303 → A-311 (student quiz + progress)
6. P-402 → P-404 (learning path)
7. I-1010, I-1001, I-1002 (E2E)

## Phase Index

| Phase | Folder | Tasks |
|-------|--------|-------|
| 0 | [phase-0-existing-system](./phase-0-existing-system/) | 1 |
| 1 | [phase-1-foundation](./phase-1-foundation/) | 15 |
| 2 | [phase-2-student-core](./phase-2-student-core/) | 6 |
| 3 | [phase-3-quiz-assessment-pdf](./phase-3-quiz-assessment-pdf/) | 35 |
| 4 | [phase-4-personalization](./phase-4-personalization/) | 9 |
| 5 | [phase-5-ai-tutor-practice](./phase-5-ai-tutor-practice/) | 10 |
| 6 | [phase-6-teacher-platform](./phase-6-teacher-platform/) | 11 |
| 7 | [phase-7-analytics](./phase-7-analytics/) | 8 |
| 8 | [phase-8-interventions](./phase-8-interventions/) | 5 |
| 9 | [phase-9-teacher-ai-tutor](./phase-9-teacher-ai-tutor/) | 4 |
| 10 | [phase-10-integration-polish](./phase-10-integration-polish/) | 12 |

**Total: 109 tasks** (50 MVP)

## How to Use with Cursor

1. Open task file e.g. `phase-1-foundation/F-001-curriculum-taxonomy-schema.md`
2. Scroll to **Cursor Implementation Prompt**
3. Copy the code block contents into Cursor Agent
4. Verify acceptance criteria before moving to next task
5. Respect dependencies listed at top of each file

## Regenerate Task Files

```bash
python docs/development-plan/_generate_task_prompts.py
```
