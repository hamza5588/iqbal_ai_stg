"""Live server check: new PDF thread, two independent lectures, view each."""
import io
import os
import sys
import time
import uuid

from app import create_app

EMAIL = os.environ.get("TEST_EMAIL", "")
PASSWORD = os.environ.get("TEST_PASSWORD", "")
STAMP = time.strftime("%H%M%S")
TITLE_A = "[LIVE] Photosynthesis %s" % STAMP
TITLE_B = "[LIVE] Water Cycle %s" % STAMP

LECTURE_A = """# Lecture on Photosynthesis

## Learning Objectives
- Explain how plants convert light energy into chemical energy.
- Identify chlorophyll, carbon dioxide, water, glucose, and oxygen in the process.

## Introduction
Photosynthesis is the process by which green plants make food using sunlight.
The overall equation is:

6CO2 + 6H2O + light energy -> C6H12O6 + 6O2

## Stages
1. Light-dependent reactions occur in the thylakoid membrane.
2. The Calvin cycle occurs in the stroma and builds glucose.

## Conclusion
Photosynthesis feeds almost every food chain on Earth.
"""

LECTURE_B = """# Lecture on the Water Cycle

## Learning Objectives
- Describe evaporation, condensation, precipitation, and collection.
- Explain how the water cycle moves water through Earth systems.

## Introduction
The water cycle is the continuous movement of water on, above, and below the surface of the Earth.

## Main Processes
1. Evaporation turns liquid water into water vapor.
2. Condensation forms clouds.
3. Precipitation returns water as rain or snow.
4. Collection stores water in oceans, lakes, and groundwater.

## Conclusion
The water cycle keeps Earth's water supply in motion and supports all living things.
"""

HEADERS = {
    "X-Forwarded-Proto": "https",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}


def fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


def make_pdf():
    import fitz  # PyMuPDF
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(
        (72, 72),
        "Science Notes for IqbalAI live check\n\n"
        "Photosynthesis: plants use sunlight, water, and carbon dioxide to make glucose and oxygen.\n"
        "Water cycle: evaporation, condensation, precipitation, and collection move water around Earth.\n",
        fontsize=12,
    )
    data = doc.tobytes()
    doc.close()
    return data


def main():
    if not EMAIL or not PASSWORD:
        fail("TEST_EMAIL / TEST_PASSWORD not set")

    app = create_app()
    with app.app_context():
        client = app.test_client()
        login = client.post(
            "/auth/login",
            data={"useremail": EMAIL, "password": PASSWORD},
            headers=HEADERS,
        )
        body = login.get_json(silent=True) or {}
        if login.status_code not in (200, 302) or (isinstance(body, dict) and body.get("success") is False):
            fail("login HTTP %s %s" % (login.status_code, body))
        print("LOGIN_OK", EMAIL)

        pdf_bytes = make_pdf()
        print("PDF_BYTES", len(pdf_bytes))
        ingest = client.post(
            "/api/rag/ingest",
            data={
                "create_new_thread": "true",
                "file": (io.BytesIO(pdf_bytes), "science_live_check.pdf"),
            },
            content_type="multipart/form-data",
            headers=HEADERS,
        )
        ingest_json = ingest.get_json(silent=True) or {}
        print("INGEST", ingest.status_code, {k: ingest_json.get(k) for k in ("success", "thread_id", "task_id", "error", "message")})
        if ingest.status_code != 200 or not ingest_json.get("success"):
            fail("ingest failed: %s" % ingest_json)
        thread_id = ingest_json.get("thread_id")
        task_id = ingest_json.get("task_id")
        if not thread_id:
            fail("no thread_id from ingest")
        print("THREAD", thread_id)

        if task_id:
            deadline = time.time() + 180
            while time.time() < deadline:
                st = client.get("/api/rag/ingest/status/" + task_id, headers=HEADERS)
                stj = st.get_json(silent=True) or {}
                state = (stj.get("state") or stj.get("status") or "").upper()
                print("INGEST_POLL", state)
                if state in ("SUCCESS", "FAILURE", "REVOKED"):
                    if state != "SUCCESS":
                        fail("ingest task %s %s" % (state, stj))
                    break
                time.sleep(4)
            else:
                fail("ingest timed out")

        ts = client.get("/api/rag/thread/status/" + thread_id, headers=HEADERS)
        tsj = ts.get_json(silent=True) or {}
        print("THREAD_STATUS", ts.status_code, "has_document", tsj.get("has_document"))

        save_a = client.post(
            "/api/lessons/create",
            json={
                "title": TITLE_A,
                "content": LECTURE_A,
                "focus_area": "Science",
                "grade_level": "Grade 8",
                "rag_thread_id": thread_id,
                "thread_id": thread_id,
            },
            headers=HEADERS,
        )
        a = save_a.get_json(silent=True) or {}
        print("SAVE_A", save_a.status_code, a.get("id"), a.get("updated_existing"), a.get("new_version"), a.get("error"))
        if save_a.status_code != 200 or not a.get("success"):
            fail("first lecture save failed: %s" % a)
        if a.get("updated_existing") or a.get("new_version"):
            fail("first save must be a new independent lesson")
        id_a = a["id"]

        save_b = client.post(
            "/api/lessons/create",
            json={
                "title": TITLE_B,
                "content": LECTURE_B,
                "focus_area": "Science",
                "grade_level": "Grade 8",
                "rag_thread_id": thread_id,
                "thread_id": thread_id,
            },
            headers=HEADERS,
        )
        b = save_b.get_json(silent=True) or {}
        print("SAVE_B", save_b.status_code, b.get("id"), b.get("updated_existing"), b.get("new_version"), b.get("error"))
        if save_b.status_code != 200 or not b.get("success"):
            fail("second lecture save failed: %s" % b)
        if b.get("updated_existing"):
            fail("second save overwrote the first lesson")
        if b.get("new_version"):
            fail("second save became a version of the first lesson")
        id_b = b["id"]
        if id_a == id_b:
            fail("both saves returned the same lesson id")

        view_a = client.get("/api/lessons/lesson/%s/view" % id_a, headers=HEADERS)
        view_b = client.get("/api/lessons/lesson/%s/view" % id_b, headers=HEADERS)
        va = view_a.get_json(silent=True) or {}
        vb = view_b.get_json(silent=True) or {}
        if view_a.status_code != 200 or not va.get("success"):
            fail("view A failed: %s" % va)
        if view_b.status_code != 200 or not vb.get("success"):
            fail("view B failed: %s" % vb)

        lesson_a = va.get("lesson") or {}
        lesson_b = vb.get("lesson") or {}
        versions_a = va.get("versions") or []
        versions_b = vb.get("versions") or []
        content_a = lesson_a.get("content") or ""
        content_b = lesson_b.get("content") or ""
        print("VIEW_A title", lesson_a.get("title"), "parent", lesson_a.get("parent_lesson_id"), "versions", len(versions_a))
        print("VIEW_B title", lesson_b.get("title"), "parent", lesson_b.get("parent_lesson_id"), "versions", len(versions_b))
        print("VIEW_A heading", content_a.split("\n", 1)[0])
        print("VIEW_B heading", content_b.split("\n", 1)[0])

        if "Photosynthesis" not in content_a:
            fail("viewing first lesson did not return photosynthesis content")
        if "Water Cycle" in content_a:
            fail("first lesson view was overwritten with water-cycle content")
        if "Water Cycle" not in content_b:
            fail("viewing second lesson did not return water-cycle content")
        if "Photosynthesis" in content_b and "Lecture on Photosynthesis" in content_b:
            fail("second lesson view contains the first lecture")
        if lesson_a.get("parent_lesson_id") or lesson_b.get("parent_lesson_id"):
            fail("lessons were linked as versions")
        if len(versions_a) != 1 or len(versions_b) != 1:
            fail("each lesson should be its own family, got versions %s and %s" % (len(versions_a), len(versions_b)))
        if str(versions_a[0].get("id")) != str(id_a):
            fail("view A versions pointed at another lesson")
        if str(versions_b[0].get("id")) != str(id_b):
            fail("view B versions pointed at another lesson")

        listing = client.get("/api/lessons/my_lessons?per_page=20", headers=HEADERS)
        listed = (listing.get_json(silent=True) or {}).get("lessons") or []
        titles = [row.get("title") for row in listed]
        print("MY_LESSONS_HAS_A", TITLE_A in titles)
        print("MY_LESSONS_HAS_B", TITLE_B in titles)
        if TITLE_A not in titles or TITLE_B not in titles:
            fail("My Lessons listing missing one of the two lectures: %s" % titles[:12])

        print("PASS two independent lectures on thread", thread_id)
        print("IDS", id_a, id_b)


if __name__ == "__main__":
    main()
