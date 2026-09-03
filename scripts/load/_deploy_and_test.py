#!/usr/bin/env python3
"""Upload LMS PDF/chat fixes to staging, restart app, smoke-test parser."""
from __future__ import annotations

import sys
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _ssh import HOST, connect

APP = "/root/iqbal_ai_stg"
LOCAL = Path(__file__).resolve().parents[2]

FILES = [
    "app/models/lms_models.py",
    "app/routes/lms_routes.py",
    "app/services/lms/assignment_service.py",
    "app/services/lms/attempt_service.py",
    "app/services/quiz/math_text.py",
    "app/utils/db.py",
    "static/admin/lms-admin-diagnostic.js",
    "static/css/wait-overlay.css",
    "static/js/wait-overlay.js",
    "static/lms/lms-core.js",
    "static/lms/lms-deficiency-chat.js",
    "static/lms/lms-panels.js",
    "static/lms/lms-student.js",
    "static/lms/lms-teacher-class.js",
    "static/lms/lms-ui.css",
    "static/teacher/css/markdown-styles.css",
    "static/teacher/js/chat-response-formatter.js",
    "templates/admin/dashboard.html",
    "templates/forgot_password/forgot_password.html",
    "templates/login/login.html",
    "templates/partials/platform_theme_head.html",
    "templates/register/register.html",
    "templates/register_email/register_email.html",
    "templates/reset_password/reset_password.html",
    "templates/student_dashboard/student_dashboard.html",
    "templates/teacher_dashboard.html",
    "templates/verify_otp/verify_otp.html",
]


def run(ssh: paramiko.SSHClient, cmd: str, timeout: int = 120) -> str:
    print(">>>", cmd, flush=True)
    _, stdout, stderr = ssh.exec_command(cmd, timeout=timeout)
    stdout.channel.settimeout(timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    if out:
        print(out[:8000], flush=True)
    if err:
        print("ERR:", err[:2000], flush=True)
    print(f"exit={code}", flush=True)
    return out


def main() -> int:
    ssh = connect(timeout=30)
    try:
        sftp = ssh.open_sftp()
        for rel in FILES:
            src = LOCAL / rel
            dst = f"{APP}/{rel}"
            print("upload", rel, src.stat().st_size, flush=True)
            sftp.put(str(src), dst)
        sftp.close()
        print("uploaded", len(FILES), "files", flush=True)
        run(ssh, f"mkdir -p {APP}/_qa_audit_tmp", timeout=30)

        run(ssh, f"cd {APP} && docker compose restart flask_app1 celery_worker nginx", timeout=180)
        run(ssh, f"cd {APP} && docker compose ps", timeout=90)
        run(
            ssh,
            "sleep 15; curl -k -s -o /dev/null -w 'health:%{http_code}\\n' https://209.23.10.34/health; "
            "curl -k -s -o /dev/null -w 'lms:%{http_code}\\n' https://209.23.10.34/api/lms/health",
            timeout=60,
        )
        run(
            ssh,
            f"grep -n 'def recover_latex' {APP}/app/services/quiz/math_text.py; "
            f"grep -n 'preserve_option_order' {APP}/app/services/quiz/mcq_converter.py; "
            f"grep -n 'def harvest_native_mcqs' {APP}/app/services/lms/mcq_utils.py",
            timeout=30,
        )
        test_src = LOCAL / "scripts" / "load" / "_remote_parser_smoke.py"
        test_src.write_text(
            "from app.services.quiz.math_text import recover_latex, recover_fields\n"
            "from app.services.lms.mcq_utils import harvest_native_mcqs\n"
            "assert 'x^{2}' in recover_latex('4x2 - 7x')\n"
            "assert '\\\\frac' in recover_latex('Simplify:\\n(a3b2)(a2b4)\\nab3')\n"
            "assert recover_latex('16 2 3 %') == '16 2/3%'\n"
            "assert recover_latex('16\\\\frac{2}{3}%') == '16 2/3%'\n"
            "assert '\\\\(' not in recover_latex('\\\\frac{\\\\((a^{3}b^{2})\\\\)\\\\((a^{2}b^{4})\\\\)}{ab^{3}}')\n"
            "stem, _ = recover_fields('Which is the correct factorization of x2 + x - 12?', None)\n"
            "assert 'Which is the correct' in stem and 'x^{2}' in stem\n"
            "pdf = (\n"
            "    '1. The number 5.181818... is\\nA. a terminating decimal\\n'\n"
            "    'B. a repeating decimal\\nC. a non-repeating decimal\\nD. an irrational number\\n'\n"
            "    'Answer: B\\n2. Choose the correct statement.\\nA. one\\nB. two\\nC. three\\nD. four\\n'\n"
            "    'Ans. D\\n3. What is log 2 + log 5?\\nA. 1\\nB. log 7\\nC. 0\\nD. 2\\n'\n"
            "    'The correct answer is 1\\n4. Solve x + 1 = 0\\nA. -1\\nB. 0\\nC. 1\\nD. 2\\n'\n"
            "    'Solution: -1\\n'\n"
            ")\n"
            "mcqs = harvest_native_mcqs(pdf)\n"
            "assert [m.get('answer') for m in mcqs] == ['B', 'D', '1', '-1'], mcqs\n"
            "print('parser_ok', len(mcqs), 'math_ok')\n",
            encoding="utf-8",
        )
        sftp = ssh.open_sftp()
        sftp.put(str(test_src), f"{APP}/_qa_audit_tmp/test_mcq_deploy.py")
        sftp.close()
        run(
            ssh,
            f"cd {APP} && docker compose exec -T flask_app1 python /app/_qa_audit_tmp/test_mcq_deploy.py",
            timeout=90,
        )
    finally:
        ssh.close()
    print("DEPLOY_AND_TEST_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
