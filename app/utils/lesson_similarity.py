"""Content-similarity helper for same-thread lesson save vs new-lesson insert.

A chat thread is reused across turns, so rag_thread_id alone cannot tell "the teacher
edited the lesson they already saved" apart from "they generated a second, different
lesson in the same chat." Callers use this to decide update-in-place vs insert-a-new-row.
"""
import difflib

# Below this ratio, two lesson contents are treated as different lessons rather than an
# edited version of the same one. Measured against real lesson-shaped markdown: a
# lightly-edited re-save ("add an example") scores ~0.94-0.98, while two genuinely
# different lessons (different topic, same markdown structure/headings) score well
# under 0.1 — so the gap leaves wide margin at 0.6.
LESSON_RESAVE_SIMILARITY_THRESHOLD = 0.6


def is_likely_same_lesson(existing_content: str, new_content: str) -> bool:
    """Heuristic: is `new_content` an edited version of `existing_content`, or a different lesson?

    Uses difflib's real ratio() (actual longest-matching-block comparison), not quick_ratio()
    — quick_ratio only compares character-frequency histograms, which two markdown lessons
    share heavily (headings, bullets, whitespace) regardless of topic, and so can't tell an
    edit apart from an unrelated lesson.
    """
    existing_content = (existing_content or "").strip()
    new_content = (new_content or "").strip()
    if not existing_content or not new_content:
        return False
    ratio = difflib.SequenceMatcher(None, existing_content, new_content, autojunk=True).ratio()
    return ratio >= LESSON_RESAVE_SIMILARITY_THRESHOLD
