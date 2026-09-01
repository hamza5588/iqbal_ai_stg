"""Time a lengthy lecture with quality gate ON vs OFF on a longer original PDF."""
import io
import os
import sys
import time

from app import create_app
from app.models.database_models import SystemSettings
from app.utils.db import get_db
from app.utils.rag_service import ANSWER_QUALITY_GATE_SETTING_KEY, _answer_quality_gate_enabled

EMAIL = os.environ.get("TEST_EMAIL", "")
PASSWORD = os.environ.get("TEST_PASSWORD", "")
PROMPT = (
    "Create a LONG classroom lecture on Newton's three laws of motion using ONLY this PDF. "
    "The lecture must be lengthy: at least 12 sections, multiple worked numerical examples, "
    "common misconceptions, a lab activity, exam-style questions with mark-scheme hints, "
    "and a recap. Do not write a short summary. Write as if this is a full 40-minute lesson."
)
HEADERS = {
    "X-Forwarded-Proto": "https",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}

PAGES = [
    "Unit title: Forces and Motion. Audience: Grade 9 physics. This source is written as connected teaching notes, not a slide deck.\n"
    "Newton published the three laws of motion in the Principia in 1687. They still form the backbone of school mechanics because they describe how objects start, stop, speed up, slow down, and interact. "
    "Before the laws, students must be clear about force as a push or pull measured in newtons (N), mass as the amount of matter in kilograms, and acceleration as the rate of change of velocity in metres per second squared. "
    "Velocity is speed with direction. A change in velocity can be a change in speed, a change in direction, or both. That is why a car turning a corner at constant speed is still accelerating. "
    "This unit treats the three laws as one story: objects keep their motion unless a net force acts; a net force causes acceleration proportional to force and inversely proportional to mass; forces always come in pairs.",

    "Chapter 1. Inertia and the first law.\n"
    "Newton's first law: an object at rest stays at rest, and an object in uniform motion in a straight line stays in that motion, unless a net external force acts on it. "
    "The property that resists changes in motion is inertia. Inertia is not a force. It is a name for the stubbornness of mass. Larger mass means larger inertia. "
    "A book on a table stays put because the net force is zero: weight down equals the normal force up. A hockey puck on ice keeps sliding because friction is small, so the net force is nearly zero. "
    "If you are standing on a bus that starts suddenly, your feet are pushed forward by friction with the floor, but your upper body tends to stay put, so you feel thrown backward. When the bus stops, you lurch forward. These are first-law effects, not a mysterious extra force. "
    "Seat belts exist because of inertia. In a crash the car stops, but an unrestrained passenger continues forward until another force (dashboard, airbag, or belt) acts.",

    "Chapter 2. Net force and balanced forces.\n"
    "A net force is the vector sum of all forces on an object. If forces cancel, net force is zero and acceleration is zero. The object may still be moving: zero acceleration means constant velocity, which includes rest. "
    "Students often think 'no force means not moving'. That is false. No net force means no change in velocity. "
    "Example: a crate pulled right with 40 N and left with 40 N has net force zero. If it was already sliding at 2 m/s, it keeps 2 m/s (ignoring friction). "
    "Free-body diagrams are the tool: draw the object as a dot or box, then arrows for weight, normal, friction, tension, applied force. Resolve into components when a force is at an angle. "
    "Only then add components along x and along y to find F_net.",

    "Chapter 3. The second law: F = ma.\n"
    "Newton's second law: the acceleration of an object is in the direction of the net force, and F_net = m a. "
    "If mass is in kilograms and acceleration in m/s^2, force is in newtons. 1 N = 1 kg m/s^2. "
    "Double the net force, double the acceleration. Double the mass, halve the acceleration for the same force. "
    "Worked example A: a 2.0 kg cart is pushed with a net force of 6.0 N. a = F/m = 6.0/2.0 = 3.0 m/s^2 in the direction of the push. "
    "Worked example B: the same 6.0 N net force on a 6.0 kg cart gives a = 1.0 m/s^2. "
    "Worked example C: a 0.50 kg trolley speeds up from rest to 4.0 m/s in 2.0 s. a = (4.0-0)/2.0 = 2.0 m/s^2. F_net = 0.50 x 2.0 = 1.0 N. "
    "Always check whether the force given is the net force. If there is also friction, F_net = F_applied - F_friction.",

    "Chapter 4. Weight is not mass.\n"
    "Weight is the gravitational force: W = m g. Near Earth, g is about 9.8 m/s^2 downward, often taken as 10 m/s^2 in rough school calculations. "
    "Mass does not change when you go to the Moon. Weight does, because g_Moon is about 1.6 m/s^2. "
    "A 50 kg student has weight 490 N on Earth and about 80 N on the Moon. The student's inertia is still 50 kg in both places. "
    "Bathroom scales measure normal force, which equals weight only when you are at rest on a horizontal surface. In a lift that accelerates upward, the scale reading is larger than mg. In free fall the scale would read zero; you are not weightless in the sense of mass disappearing, you are in free fall with the scale.",

    "Chapter 5. Friction as a real force.\n"
    "Friction opposes relative sliding (kinetic) or the tendency to slide (static). It acts parallel to the contact surface. "
    "Static friction can take any value up to a maximum mu_s N, where N is the normal force. Kinetic friction is often written mu_k N and is usually smaller than the maximum static value. "
    "Worked example D: a 10 kg box on a horizontal floor, mu_k = 0.20, g = 10 m/s^2. Normal force = 100 N. Kinetic friction = 20 N. If you pull with 50 N, F_net = 30 N, a = 3.0 m/s^2. "
    "If you pull with only 15 N and the box is at rest, static friction can match 15 N, so the box does not move. "
    "Friction is not always an enemy. Walking requires friction; without it your foot would slip and you could not push the Earth backward.",

    "Chapter 6. The third law: action and reaction.\n"
    "Newton's third law: if body A exerts a force on body B, then B exerts an equal-size force on A in the opposite direction. The two forces act on different objects. That is why they do not cancel in one free-body diagram. "
    "When you jump, you push the Earth down; the Earth pushes you up. The forces are equal, but the Earth's huge mass means its acceleration is tiny. "
    "A rocket expels gas backward; the gas pushes the rocket forward. This works in vacuum; the rocket does not need to push against air. "
    "A book on a table: the book's weight is Earth pulling the book. The reaction to weight is the book pulling Earth upward. The normal force from the table on the book is a different pair: book on table and table on book. "
    "Do not pair weight with normal force as a third-law pair. They are often equal in size at rest, but they are not an action-reaction pair.",

    "Chapter 7. Connecting the three laws in one event.\n"
    "A person pushes a supermarket trolley. Third law: person-on-trolley and trolley-on-person. Second law on the trolley: if the push is larger than friction, the trolley accelerates. First law: if the person stops pushing and friction is small, the trolley keeps rolling until friction (or a stop) changes its velocity. "
    "A football is kicked. The foot exerts a large force for a short time (impulse). The ball accelerates. After leaving the foot, if we ignore air, net force is nearly just weight, so it follows a projectile path. The first law does not say it travels in a straight line forever, because gravity is a net force.",

    "Chapter 8. Worked numerical set 1.\n"
    "Problem 1. A 4.0 kg box is pulled right with 24 N. Friction is 8.0 N. Find acceleration. F_net = 16 N, a = 4.0 m/s^2 right. "
    "Problem 2. How large a net force is needed to give a 1500 kg car an acceleration of 2.5 m/s^2? F = 3750 N. "
    "Problem 3. A 0.20 kg ball is thrown straight up. After it leaves the hand, what is the net force if air is ignored? Only weight, 2.0 N down if g = 10. Acceleration is g down even while it is still going up. Velocity and acceleration do not have to point the same way. "
    "Problem 4. Two horizontal forces, 12 N east and 5 N west, act on a 1.4 kg object. F_net = 7 N east, a = 5.0 m/s^2 east.",

    "Chapter 9. Worked numerical set 2, including lifts.\n"
    "Problem 5. A 60 kg person stands in a lift. g = 10 m/s^2. If the lift is at rest or moving at constant velocity, scale reading = 600 N. "
    "If the lift accelerates upward at 2.0 m/s^2, F_net upward = m a = 120 N, so normal force N - 600 = 120, N = 720 N. You feel heavier. "
    "If the lift accelerates downward at 2.0 m/s^2, 600 - N = 120, N = 480 N. You feel lighter. "
    "Problem 6. A 3.0 kg block on a frictionless table is pulled by a string at 30 degrees above the horizontal with tension 10 N. Horizontal component = 10 cos 30 = 8.66 N. a_x = 2.89 m/s^2. Vertical component reduces apparent need for normal force: N + 10 sin 30 = 30, N = 25 N.",

    "Chapter 10. Circular motion is still Newton's laws.\n"
    "Uniform circular motion has constant speed but changing direction, so there is centripetal acceleration a = v^2/r toward the centre. A net force F = m v^2/r must point toward the centre. "
    "That force might be tension in a string, friction for a car on a flat bend, or gravity for an orbit. There is no extra 'centrifugal force' on the object in an inertial frame. The feeling of being flung outward in a turning car is inertia: your body wants to go straight while the car door pushes you inward.",

    "Chapter 11. Momentum as a companion idea.\n"
    "Momentum p = m v. Newton's second law can be written F_net = dp/dt. If mass is constant this is again ma. "
    "Impulse is F_avg x time = change in momentum. A longer collision time (airbag, crumple zone) reduces the average force for the same change in momentum. "
    "If two ice skaters push apart, their momenta are equal and opposite if we ignore external forces, because the third-law pair of forces acts for the same time. That is conservation of momentum in a simple form.",

    "Chapter 12. Classroom lab: trolley and ticker tape or motion sensor.\n"
    "Aim: test F = ma by varying force at constant mass, then varying mass at constant force. "
    "Method: a string over a pulley with hanging masses provides a known accelerating force. Measure acceleration from a velocity-time graph. "
    "Control: keep friction small; include the hanging mass as part of the system mass if it also accelerates. "
    "Expected graph: a against F is a straight line through the origin if mass is constant. Gradient is 1/m. "
    "Safety: catch the trolley; do not stand in the path of falling masses.",

    "Chapter 13. Common misconceptions to attack in class.\n"
    "1. Force is needed to keep something moving. Reply: only if friction or drag is present; then the force balances those, net force can be zero at constant speed. "
    "2. Heavier objects always fall faster. Reply: in vacuum all objects have the same g; air resistance depends on shape and speed. "
    "3. Action-reaction forces cancel. Reply: they act on different objects. "
    "4. Mass and weight are the same word. Reply: mass in kg, weight a force in N. "
    "5. There is a force of inertia. Reply: inertia is not a force. "
    "6. Acceleration always means speeding up. Reply: slowing down is acceleration opposite to velocity; turning is acceleration too.",

    "Chapter 14. Everyday applications.\n"
    "Seat belts and airbags: increase time of momentum change, reduce peak force (second law as impulse). "
    "Walking and swimming: push water or ground backward, get pushed forward (third law). "
    "Helicopters: push air down, air pushes rotors up. "
    "Sports: a longer follow-through can increase the time of contact, changing impulse. "
    "Building design: base isolation and damping change how forces accelerate a structure during an earthquake.",

    "Chapter 15. Exam-style questions.\n"
    "Q1. State Newton's first law and give an everyday example that involves motion, not rest. "
    "Q2. A 5.0 kg box is pushed with 20 N. Friction is 5.0 N. Calculate acceleration and state its direction. "
    "Q3. Explain why a rocket can accelerate in space where there is no air to push against. "
    "Q4. Distinguish the reaction to a book's weight from the normal force on the book. "
    "Q5. A lift cable snaps. What is the person's apparent weight during the fall, ignoring air? Explain with F = ma. "
    "Q6. Sketch a free-body diagram of a skydiver at terminal velocity. What is the net force?",

    "Chapter 16. Mark-scheme hints.\n"
    "Q2: F_net = 15 N, a = 3.0 m/s^2 in the direction of the push. Award a mark for subtracting friction. "
    "Q3: gases are pushed backward; gases push rocket forward; third law; no air required. "
    "Q4: weight pair is Earth-book gravity; normal pair is table-book contact. Equal size at rest is not enough to call them a third-law pair. "
    "Q5: only gravity acts, a = g down, normal force 0, apparent weight 0. "
    "Q6: weight down, drag up, equal arrows, F_net = 0, constant velocity.",

    "Chapter 17. Teacher pacing for a 40-minute lesson.\n"
    "0-5 min: demo a trolley that keeps rolling after a short push, ask why it eventually stops. "
    "5-12 min: first law and inertia stories (bus, seat belt). "
    "12-22 min: second law with two live calculations on the board, students try problem 1 in pairs. "
    "22-30 min: third law with jump and rocket, kill the cancel myth. "
    "30-37 min: one lift or friction numerical. "
    "37-40 min: recap three sentences, one per law. Homework: Q2, Q3, Q6.",

    "Chapter 18. Recap paragraph bank.\n"
    "Law 1 is about keeping velocity when net force is zero. Law 2 is the recipe that turns net force and mass into acceleration. Law 3 is about pairs of forces on two bodies. "
    "If students can draw a correct free-body diagram, choose F = ma only for the object they drew, and refuse to cancel a third-law partner that is not on that diagram, they are ready for the next topic: energy and work. "
    "Energy methods will later feel easier because they skip some vector bookkeeping, but they do not replace Newton's laws; they sit on top of them.",

    "Chapter 19. Extra practice: mixed word problems.\n"
    "A 800 kg elevator is lifted by a cable. If it accelerates upward at 0.50 m/s^2, find cable tension. Use g = 10. T - 8000 = 400, T = 8400 N. "
    "A 0.40 kg toy rocket expels gas so that the net upward force is 6.0 N. Find acceleration. a = 15 m/s^2 up. "
    "Two teams pull a 60 kg rope in a tug of war. If the rope is not accelerating, the two pulls are equal. Each team still feels a large force; equal forces on the rope do not mean zero force on a person. "
    "A raindrop reaches terminal speed. Net force is zero even though many forces act.",

    "Chapter 20. Glossary for the unit.\n"
    "Acceleration, force, free-body diagram, friction (static and kinetic), g, impulse, inertia, mass, net force, newton, normal force, terminal velocity, tension, vector, velocity, weight. "
    "Students should be able to write one equation and one sentence for each term. Example: tension is a pull along a string; it appears as an arrow away from the object along the string on a free-body diagram.",

    "Chapter 21. Historical note and limits of the laws.\n"
    "Newton's laws are for inertial frames: laboratories that are not accelerating, or are only approximately so. A turning carousel is a poor frame for applying F = ma without extra fictitious forces. "
    "At speeds near light, special relativity replaces classical momentum. At atomic scales, quantum rules appear. For Grade 9, the three laws are the correct model for trolleys, footballs, lifts, and cars. "
    "Einstein did not throw Newton away for school mechanics; he showed where the model stops. That honesty belongs in one short paragraph so students do not think physics is a pile of exceptions.",

    "Chapter 22. Linking to graphs.\n"
    "A horizontal line on a velocity-time graph means zero acceleration and therefore zero net force. A sloping v-t line means constant acceleration and constant net force. "
    "The area under a v-t graph is displacement. The gradient of a s-t graph is velocity. "
    "If students can switch between a story, a free-body diagram, F = ma, and a v-t graph, they have the full Grade 9 toolkit for this unit.",

    "Chapter 23. Safety and practical ethics.\n"
    "Do not demonstrate inertia with students standing unrestrained on a moving vehicle. Use a dynamics trolley, a phone in a box, or a video of a crash-test dummy. "
    "Hanging masses on pulleys must be caught. Springs should not be overstretched toward faces. "
    "When discussing car safety, keep the tone factual; some students may have family experience of accidents. The physics of impulse still matters.",

    "Chapter 24. Summary table for the three laws.\n"
    "First law: if F_net = 0 then a = 0, velocity constant. Key word: inertia. "
    "Second law: a = F_net / m. Key word: proportional. "
    "Third law: F_AB = - F_BA, different objects. Key word: pair. "
    "Memory trap: 'for every action there is an equal and opposite reaction' is incomplete unless students name the two objects. "
    "End of source notes. Teachers may photocopy the numerical sets in chapters 8, 9, and 19 as a worksheet.",
]


def fail(msg):
    print("FAIL:", msg)
    sys.exit(1)


def make_pdf():
    import fitz
    doc = fitz.open()
    for i, text in enumerate(PAGES, start=1):
        page = doc.new_page()
        page.insert_text(
            (54, 40),
            "IqbalAI Physics Source  |  Grade 9 Forces and Motion  |  Page %s/%s" % (i, len(PAGES)),
            fontsize=9,
        )
        rect = fitz.Rect(48, 58, 547, 780)
        page.insert_textbox(rect, text, fontsize=10, align=0)
    data = doc.tobytes()
    doc.close()
    return data


def set_quality_gate(enabled):
    db = get_db()
    row = db.query(SystemSettings).filter(SystemSettings.key == ANSWER_QUALITY_GATE_SETTING_KEY).first()
    value = "true" if enabled else "false"
    if row:
        row.value = value
    else:
        db.add(SystemSettings(
            key=ANSWER_QUALITY_GATE_SETTING_KEY,
            value=value,
            description="Answer quality gate timed test",
        ))
    db.commit()
    actual = _answer_quality_gate_enabled()
    print("GATE_SET", value, "READ_BACK", actual)
    if actual != enabled:
        fail("quality gate did not stick: wanted %s got %s" % (enabled, actual))


def login(client):
    resp = client.post(
        "/auth/login",
        data={"useremail": EMAIL, "password": PASSWORD},
        headers=HEADERS,
    )
    body = resp.get_json(silent=True) or {}
    if resp.status_code not in (200, 302) or (isinstance(body, dict) and body.get("success") is False):
        fail("login HTTP %s %s" % (resp.status_code, body))
    print("LOGIN_OK")


def ingest(client, pdf_bytes, label):
    started = time.time()
    resp = client.post(
        "/api/rag/ingest",
        data={
            "create_new_thread": "true",
            "file": (io.BytesIO(pdf_bytes), "newton_laws_grade9_source.pdf"),
        },
        content_type="multipart/form-data",
        headers=HEADERS,
    )
    body = resp.get_json(silent=True) or {}
    if resp.status_code != 200 or not body.get("success"):
        fail("%s ingest failed: %s" % (label, body))
    thread_id = body.get("thread_id")
    task_id = body.get("task_id")
    conv_id = body.get("conversation_id")
    print(label, "THREAD", thread_id, "TASK", task_id)
    if task_id:
        deadline = time.time() + 300
        while time.time() < deadline:
            st = client.get("/api/rag/ingest/status/" + task_id, headers=HEADERS)
            stj = st.get_json(silent=True) or {}
            state = (stj.get("state") or stj.get("status") or "").upper()
            print(label, "INGEST_POLL", state)
            if state in ("SUCCESS", "FAILURE", "REVOKED"):
                if state != "SUCCESS":
                    fail("%s ingest task %s %s" % (label, state, stj))
                break
            time.sleep(4)
        else:
            fail("%s ingest timed out" % label)
    print(label, "INGEST_SEC", round(time.time() - started, 1))
    return thread_id, conv_id


def create_lecture(client, thread_id, conv_id, label):
    payload = {"message": PROMPT, "thread_id": thread_id}
    if conv_id:
        payload["conversation_id"] = conv_id
    started = time.time()
    resp = client.post("/api/rag/chat", json=payload, headers=HEADERS)
    elapsed = time.time() - started
    body = resp.get_json(silent=True) or {}
    text = body.get("message") or body.get("response") or ""
    words = len(str(text).split())
    print(label, "HTTP", resp.status_code, "SEC", round(elapsed, 1), "CHARS", len(str(text)), "WORDS", words)
    print(label, "ERROR", body.get("error"))
    heading = str(text).split("\n", 1)[0][:140]
    print(label, "HEADING", heading)
    if resp.status_code != 200 or body.get("error"):
        fail("%s lecture chat failed: %s" % (label, body.get("error") or body))
    return elapsed, str(text)


def main():
    if not EMAIL or not PASSWORD:
        fail("TEST_EMAIL / TEST_PASSWORD not set")
    pdf_bytes = make_pdf()
    print("PDF_BYTES", len(pdf_bytes), "PAGES", len(PAGES))
    app = create_app()
    with app.app_context():
        client = app.test_client()
        login(client)

        set_quality_gate(True)
        thread_on, conv_on = ingest(client, pdf_bytes, "ON")
        t_on, text_on = create_lecture(client, thread_on, conv_on, "ON")

        set_quality_gate(False)
        thread_off, conv_off = ingest(client, pdf_bytes, "OFF")
        t_off, text_off = create_lecture(client, thread_off, conv_off, "OFF")

        print("==== RESULT ====")
        print("GATE_ON_SECONDS", round(t_on, 1))
        print("GATE_OFF_SECONDS", round(t_off, 1))
        print("DELTA_SECONDS", round(t_on - t_off, 1))
        print("ON_WORDS", len(text_on.split()), "OFF_WORDS", len(text_off.split()))
        blob = (text_on + " " + text_off).lower()
        print("HAS_NEWTON", "newton" in blob)
        print("HAS_SECOND_LAW", "second law" in blob or "f = ma" in blob or "f=ma" in blob)
        set_quality_gate(True)
        print("GATE_RESTORED_ON")


if __name__ == "__main__":
    main()
