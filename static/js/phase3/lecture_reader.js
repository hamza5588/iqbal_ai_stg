(function () {
  const lessonId = window.__LESSON_ID || (function () {
    const m = window.location.pathname.match(/lecture-reader\/(\d+)/);
    return m ? parseInt(m[1], 10) : 0;
  })();

  const modeSel = document.getElementById("modeSel");
  const contentPane = document.getElementById("contentPane");
  const q = document.getElementById("q");
  const ans = document.getElementById("ans");
  const meter = document.getElementById("meter");
  let sessionKey = "lr_" + Math.random().toString(36).slice(2);
  let tStart = Date.now();
  let qCount = 0;

  function tickMeter() {
    const sec = Math.floor((Date.now() - tStart) / 1000);
    meter.textContent = "session_s: " + sec + "\nquestions: " + qCount + "\nmastery_est: heuristic";
  }
  setInterval(tickMeter, 5000);
  tickMeter();

  function sendEvents(extra) {
    const payload = {
      type: "study.heartbeat",
      session_key: sessionKey,
      lesson_id: lessonId,
      t_s: Math.floor((Date.now() - tStart) / 1000),
    };
    fetch("/api/phase3/events/batch", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ events: [Object.assign(payload, extra || {})] }),
    }).catch(function () {});
  }
  setInterval(function () { sendEvents(); }, 60000);

  if (!lessonId) {
    contentPane.innerHTML = "<p>Set a valid lesson id in the URL.</p>";
    return;
  }

  fetch("/api/lessons/lesson/" + lessonId, { credentials: "same-origin" })
    .then(function (r) {
      if (!r.ok) throw new Error("lesson");
      return r.json();
    })
    .then(function (d) {
      const lesson = d.lesson || d;
      const html = marked.parse(lesson.content || "");
      contentPane.innerHTML = DOMPurify.sanitize(html);
    })
    .catch(function () {
      contentPane.textContent = "Unable to load lesson (check access).";
    });

  document.getElementById("askBtn").onclick = function () {
    const text = (q.value || "").trim();
    if (!text) return;
    qCount += 1;
    tickMeter();
    fetch("/api/lessons/ask_question", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({
        lesson_id: lessonId,
        question: text,
        learning_mode: modeSel.value === "self_study" ? "self_study" : "lecture",
        session_key: sessionKey,
        source_context: { surface: "lecture_reader" },
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        ans.textContent = d.answer || d.error || JSON.stringify(d);
        sendEvents({ type: "student.question.sent", question_len: text.length });
      })
      .catch(function (e) { ans.textContent = String(e); });
  };

  document.getElementById("ttsBtn").onclick = function () {
    const plain = (ans.textContent || "").slice(0, 4000);
    if (!plain) return;
    fetch("/text-to-speech", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: plain, language: "auto" }),
    })
      .then(function (r) { return r.blob(); })
      .then(function (blob) {
        const url = URL.createObjectURL(blob);
        const a = document.getElementById("ttsAudio");
        a.src = url;
        a.play();
      })
      .catch(function () {});
  };

  document.getElementById("micBtn").onclick = function () {
    alert("Connect mic + MediaRecorder in production; demo uses prompt.");
    const t = prompt("Simulated speech-to-text:");
    if (t) q.value = t;
  };

  document.addEventListener("mouseup", function () {
    const sel = window.getSelection().toString().trim();
    if (sel.length > 3) {
      q.value = "Explain: " + sel;
    }
  });
})();
