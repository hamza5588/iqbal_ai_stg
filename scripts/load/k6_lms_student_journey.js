/**
 * IqbalAI LMS — 200 concurrent student journey (staging).
 *
 * Env:
 *   BASE_URL            default https://209.23.10.34
 *   STUDENT_PASSWORD    required
 *   SMOKE               "1" → 1 VU / 4m validation run
 *   EXPLAIN_ALL         "0" → 20% of VUs call LLM explain (default: all 200 VUs)
 *
 * Staging TLS is a self-signed / IP cert; insecureSkipTLSVerify is on.
 * Passwords are injected via env — do not hardcode them in this file.
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

const BASE = (__ENV.BASE_URL || "https://209.23.10.34").replace(/\/$/, "");
const PASSWORD = __ENV.STUDENT_PASSWORD || "";
const SMOKE = __ENV.SMOKE === "1";
const EXPLAIN_ALL = __ENV.EXPLAIN_ALL !== "0";

export const options = SMOKE
  ? {
      insecureSkipTLSVerify: true,
      vus: 1,
      duration: "4m",
      thresholds: {
        http_req_failed: ["rate<0.15"],
      },
    }
  : {
      insecureSkipTLSVerify: true,
      scenarios: {
        students: {
          executor: "ramping-vus",
          startVUs: 0,
          gracefulRampDown: "30s",
          stages: [
            { duration: "15s", target: 20 },
            { duration: "1m45s", target: 20 },
            { duration: "2m", target: 200 },
            { duration: "20m", target: 200 },
            { duration: "4m", target: 0 },
          ],
        },
      },
      thresholds: {
        http_req_failed: [
          { threshold: "rate<0.05", abortOnFail: true, delayAbortEval: "2m" },
        ],
      },
    };

const loginTrend = new Trend("scenario_login", true);
const dashTrend = new Trend("scenario_dashboard", true);
const diagTrend = new Trend("scenario_diagnostic", true);
const chatTrend = new Trend("scenario_learning_chat", true);
const explainTrend = new Trend("scenario_llm_explain", true);
const quizTrend = new Trend("scenario_quiz", true);
const nonLlmTrend = new Trend("scenario_non_llm", true);

const loginFail = new Rate("fail_login");
const diagStartFail = new Rate("fail_diagnostic_start");
const diagSubmitFail = new Rate("fail_diagnostic_submit");
const chatStartFail = new Rate("fail_learning_chat_start");
const quizFail = new Rate("fail_quiz_complete");
const http5xx = new Rate("http_5xx");
const http429 = new Rate("http_429");

const journeyComplete = new Counter("journey_complete");
const journeyStarted = new Counter("journey_started");
const diagCompleted = new Counter("diagnostic_completed");
const chatCompleted = new Counter("learning_chat_completed");
const quizCompleted = new Counter("quiz_completed");
const explainOk = new Counter("explain_success");
const explainFail = new Counter("explain_failure");

const jsonHeaders = {
  Accept: "application/json",
  "X-Requested-With": "XMLHttpRequest",
  "Content-Type": "application/json",
  "X-Load-Test": "1",
};

function unwrap(res) {
  try {
    const body = res.json();
    if (body && typeof body === "object" && Object.prototype.hasOwnProperty.call(body, "data")) {
      return body.data;
    }
    return body;
  } catch (e) {
    return null;
  }
}

function errText(res) {
  try {
    const body = res.json();
    if (body && body.error) {
      if (typeof body.error === "object") return body.error.message || JSON.stringify(body.error);
      return String(body.error);
    }
    return JSON.stringify(body).slice(0, 180);
  } catch (e) {
    return (res.body || "").toString().slice(0, 180);
  }
}

function recordStatus(res) {
  const code = res.status;
  http5xx.add(code >= 500);
  http429.add(code === 429);
}

function timedGet(path, tag, timeout) {
  const t0 = Date.now();
  const res = http.get(`${BASE}${path}`, {
    headers: jsonHeaders,
    tags: { name: tag },
    timeout: timeout || "60s",
  });
  const sec = (Date.now() - t0) / 1000;
  recordStatus(res);
  if (tag !== "LLMExplain") nonLlmTrend.add(sec * 1000);
  return { res, sec };
}

function timedPostJson(path, body, tag, timeout) {
  const t0 = Date.now();
  const res = http.post(`${BASE}${path}`, JSON.stringify(body || {}), {
    headers: jsonHeaders,
    tags: { name: tag },
    timeout: timeout || "60s",
  });
  const sec = (Date.now() - t0) / 1000;
  recordStatus(res);
  if (tag !== "LLMExplain") nonLlmTrend.add(sec * 1000);
  return { res, sec };
}

function studentEmail() {
  const n = ((__VU - 1) % 200) + 1;
  return `loadtest_student_${String(n).padStart(3, "0")}@test.iqbalai.local`;
}

function login(email) {
  const t0 = Date.now();
  const res = http.post(
    `${BASE}/auth/login`,
    { useremail: email, password: PASSWORD },
    {
      headers: {
        Accept: "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-Load-Test": "1",
      },
      tags: { name: "Login" },
      timeout: "60s",
    }
  );
  const sec = (Date.now() - t0) / 1000;
  loginTrend.add(sec * 1000);
  recordStatus(res);
  nonLlmTrend.add(sec * 1000);
  let ok = false;
  try {
    ok = res.status === 200 && res.json("success") === true;
  } catch (e) {
    ok = res.status === 200;
  }
  loginFail.add(!ok);
  check(res, { "login 200 json": () => ok });
  return ok;
}

function pollTimer(attemptId, seconds) {
  let left = seconds;
  while (left > 0) {
    timedGet(`/api/lms/attempts/${attemptId}/timer`, "DiagnosticTimer", "30s");
    const chunk = Math.min(5, left);
    sleep(chunk);
    left -= chunk;
  }
}

let didJourney = false;

export default function () {
  if (!PASSWORD) {
    throw new Error("STUDENT_PASSWORD env var is required");
  }

  if (didJourney) {
    sleep(15);
    return;
  }
  didJourney = true;

  const email = studentEmail();
  journeyStarted.add(1);

  if (!login(email)) {
    return;
  }
  sleep(Math.random() * 3 + 2);

  const d1 = timedGet("/api/lms/students/me/dashboard", "Dashboard", "60s");
  dashTrend.add(d1.sec * 1000);
  check(d1.res, { "dashboard 200": (r) => r.status === 200 });
  const d2 = timedGet("/api/lms/students/me/onboarding-status", "Onboarding", "60s");
  dashTrend.add(d2.sec * 1000);
  check(d2.res, { "onboarding 200": (r) => r.status === 200 });
  sleep(Math.random() * 2 + 2);

  const dg = timedGet("/api/lms/diagnostics/default", "DiagnosticDefault", "60s");
  const diag = unwrap(dg.res) || {};
  const diagnosticId = diag.id;
  if (!diagnosticId) {
    diagStartFail.add(true);
    return;
  }

  const start = timedPostJson(`/api/lms/quizzes/${diagnosticId}/start`, {}, "DiagnosticStart", "60s");
  diagTrend.add(start.sec * 1000);
  const startOk = start.res.status === 200 || start.res.status === 201;
  const startData = unwrap(start.res) || {};
  const attemptId = startData.attempt_id;
  diagStartFail.add(!(startOk && attemptId));
  check(start.res, { "diagnostic start": () => startOk && attemptId });
  if (!attemptId) {
    return;
  }

  const qres = timedGet(`/api/lms/attempts/${attemptId}/questions`, "DiagnosticQuestions", "90s");
  diagTrend.add(qres.sec * 1000);
  const qbody = unwrap(qres.res) || {};
  const questions = qbody.questions || [];
  check(qres.res, { "diagnostic questions": (r) => r.status === 200 && questions.length > 0 });

  for (let i = 0; i < questions.length; i++) {
    const qid = questions[i].question_id;
    const pick = Math.floor(Math.random() * 4);
    pollTimer(attemptId, Math.random() * 12 + 3);
    const ans = timedPostJson(
      `/api/lms/attempts/${attemptId}/answer`,
      { question_id: qid, selected_option_index: pick },
      "DiagnosticAnswer",
      "60s"
    );
    diagTrend.add(ans.sec * 1000);
    check(ans.res, { "diagnostic answer 200": (r) => r.status === 200 });
  }

  const sub = timedPostJson(`/api/lms/attempts/${attemptId}/submit`, {}, "DiagnosticSubmit", "120s");
  diagTrend.add(sub.sec * 1000);
  const subOk = sub.res.status === 200;
  diagSubmitFail.add(!subOk);
  check(sub.res, { "diagnostic submit": (r) => r.status === 200 });
  if (subOk) diagCompleted.add(1);
  sleep(Math.random() * 3 + 2);

  const again = http.post(`${BASE}/api/lms/quizzes/${diagnosticId}/start`, JSON.stringify({}), {
    headers: jsonHeaders,
    tags: { name: "DiagnosticRetakeDenied" },
    timeout: "60s",
    responseCallback: http.expectedStatuses(400),
  });
  recordStatus(again);
  const againMsg = errText(again).toLowerCase();
  check(again, {
    "retake denied": (r) =>
      r.status === 400 && (againMsg.indexOf("already") >= 0 || againMsg.indexOf("completed") >= 0),
  });

  // force_new:false mirrors the real product ("openDeficiencyChat") and lets the
  // post-diagnostic prewarm serve a cached queue instead of regenerating MCQs.
  const chatStart = timedPostJson("/api/lms/deficiency/sessions", { force_new: false }, "LearningChatStart", "120s");
  chatTrend.add(chatStart.sec * 1000);
  const chatData = unwrap(chatStart.res) || {};
  const sessionId = chatData.session_id;
  const chatOk = (chatStart.res.status === 200 || chatStart.res.status === 201) && sessionId;
  chatStartFail.add(!chatOk);
  check(chatStart.res, { "learning chat start": () => chatOk });
  // Learning Chat is an optional enrichment step. A degraded or failed chat
  // start must NOT stop the student from reaching the assigned quiz — those are
  // independent product flows. Only run the chat sub-journey when we have a
  // session; always fall through to the quiz.
  if (sessionId) {
    timedGet(`/api/lms/deficiency/sessions/${sessionId}`, "LearningChatGet", "60s");

    let chatAnswersOk = 0;
    for (let i = 0; i < 3; i++) {
      sleep(Math.random() * 3 + 2);
      const ca = timedPostJson(
        `/api/lms/deficiency/sessions/${sessionId}/answer`,
        { selected_option_index: Math.floor(Math.random() * 4) },
        "LearningChatAnswer",
        "90s"
      );
      chatTrend.add(ca.sec * 1000);
      if (ca.res.status === 200) {
        chatAnswersOk += 1;
        const payload = unwrap(ca.res) || {};
        if (payload.last_answer && payload.last_answer.correct === false) {
          const adv = timedPostJson(
            `/api/lms/deficiency/sessions/${sessionId}/advance`,
            {},
            "LearningChatAdvance",
            "60s"
          );
          chatTrend.add(adv.sec * 1000);
        }
      }
    }
    if (chatAnswersOk >= 3) chatCompleted.add(1);

    if (EXPLAIN_ALL || __VU % 5 === 0) {
      const ex = timedPostJson(
        `/api/lms/deficiency/sessions/${sessionId}/explain`,
        { message: "Explain this step by step" },
        "LLMExplain",
        "120s"
      );
      explainTrend.add(ex.sec * 1000);
      if (ex.res.status === 200) explainOk.add(1);
      else explainFail.add(1);
      sleep(Math.random() * 2 + 2);
    }
  }

  const asg = timedGet("/api/lms/students/me/assignments", "Assignments", "60s");
  const items = unwrap(asg.res) || [];
  let quizId = null;
  let assignmentId = null;
  if (Array.isArray(items)) {
    for (let i = 0; i < items.length; i++) {
      if (items[i].status !== "submitted" && items[i].quiz_id) {
        quizId = items[i].quiz_id;
        assignmentId = items[i].assignment_id;
        break;
      }
    }
    if (!quizId && items.length) {
      quizId = items[0].quiz_id;
      assignmentId = items[0].assignment_id;
    }
  }

  if (!quizId) {
    quizFail.add(true);
    return;
  }

  const qStart = timedPostJson(
    `/api/lms/quizzes/${quizId}/start`,
    assignmentId ? { assignment_id: assignmentId } : {},
    "QuizStart",
    "60s"
  );
  quizTrend.add(qStart.sec * 1000);
  const qData = unwrap(qStart.res) || {};
  const quizAttempt = qData.attempt_id;
  check(qStart.res, { "quiz start": (r) => (r.status === 200 || r.status === 201) && quizAttempt });
  if (!quizAttempt) {
    quizFail.add(true);
    return;
  }

  const qq = timedGet(`/api/lms/attempts/${quizAttempt}/questions`, "QuizQuestions", "90s");
  const qlist = (unwrap(qq.res) || {}).questions || [];
  for (let i = 0; i < qlist.length; i++) {
    sleep(Math.random() * 4 + 2);
    const qa = timedPostJson(
      `/api/lms/attempts/${quizAttempt}/answer`,
      { question_id: qlist[i].question_id, selected_option_index: Math.floor(Math.random() * 4) },
      "QuizAnswer",
      "60s"
    );
    quizTrend.add(qa.sec * 1000);
  }

  const qsub = timedPostJson(`/api/lms/attempts/${quizAttempt}/submit`, {}, "QuizSubmit", "90s");
  quizTrend.add(qsub.sec * 1000);
  const quizOk = qsub.res.status === 200;
  quizFail.add(!quizOk);
  check(qsub.res, { "quiz submit": (r) => r.status === 200 });
  if (quizOk) {
    quizCompleted.add(1);
    journeyComplete.add(1);
  }

  if (__VU % 10 === 0) {
    http.get(`${BASE}/auth/logout`, {
      headers: jsonHeaders,
      tags: { name: "Logout" },
      timeout: "30s",
    });
  } else if (!SMOKE) {
    sleep(Math.random() * 20 + 20);
    timedGet("/api/lms/students/me/dashboard", "DashboardIdle", "60s");
  }
}

export function handleSummary(data) {
  return {
    "scripts/load/results/k6_summary.json": JSON.stringify(data, null, 2),
    stdout: textFromSummary(data),
  };
}

function textFromSummary(data) {
  const lines = [];
  lines.push("=== k6 LMS load test summary ===");
  const names = Object.keys(data.metrics || {}).sort();
  for (let i = 0; i < names.length; i++) {
    const n = names[i];
    if (
      n.indexOf("scenario_") === 0 ||
      n.indexOf("fail_") === 0 ||
      n.indexOf("http_5xx") === 0 ||
      n.indexOf("http_429") === 0 ||
      n.indexOf("journey_") === 0 ||
      n.indexOf("diagnostic_") === 0 ||
      n.indexOf("learning_") === 0 ||
      n.indexOf("quiz_") === 0 ||
      n.indexOf("explain_") === 0 ||
      n === "http_reqs" ||
      n === "http_req_failed" ||
      n === "http_req_duration" ||
      n === "vus_max"
    ) {
      lines.push(`${n}: ${JSON.stringify(data.metrics[n].values)}`);
    }
  }
  return lines.join("\n") + "\n";
}
