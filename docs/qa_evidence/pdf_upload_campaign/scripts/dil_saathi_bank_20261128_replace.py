"""Replace the DIL SAATHI Mathematics IX target PDF (diagnostic 192) and review its bank.

Same path as Admin -> Diagnostic -> Replace target PDF; then each needs_review MCQ is
corrected from the PDF comparison (tb_fixes.json) and approved individually.
"""
import json, os, sys, collections
sys.path.insert(0, "/app")
os.environ.setdefault("PYTHONPATH", "/app")
from app import create_app

ASSESSMENT_ID = 192
OLD_FILENAME = "DIL SAATHI Practice Question Bank.pdf"
PDF = "/app/tmp/tb_replace/DIL SAATHI Practice Question Bank Updated-28112026.pdf"
FIXES = json.load(open("/app/tmp/tb_replace/tb_fixes.json", encoding="utf-8"))
MODE = sys.argv[1] if len(sys.argv) > 1 else "check"

app = create_app()
with app.app_context():
    from app.utils.db import get_db
    from app.models.database_models import User
    from app.models.lms_models import DiagnosticTargetPdf, TargetBankDocument, TargetBankItem
    from app.services.lms import target_bank_service as tbs
    from app.services.lms.diagnostic_pdf_service import replace_target_pdf
    from app.utils.llm_gateway import llm_workflow

    db = get_db()
    admin = db.query(User).filter(User.role == "admin", User.is_active.is_(True)).order_by(User.id).first()
    tp = db.query(DiagnosticTargetPdf).filter(DiagnosticTargetPdf.assessment_id == ASSESSMENT_ID).all()
    print("admin", admin.id, admin.useremail, "| target pdfs on", ASSESSMENT_ID, [(t.id, t.original_filename) for t in tp])
    old = [t for t in tp if t.original_filename == OLD_FILENAME]
    if MODE == "check":
        sys.exit(0)

    if MODE == "replace":
        assert len(old) == 1, "expected exactly one old target PDF"
        with llm_workflow("ai_guided_practice_topic_extraction", user_id=admin.id, user_role="admin"):
            res = replace_target_pdf(ASSESSMENT_ID, admin.id, old[0].id, open(PDF, "rb").read(),
                                     os.path.basename(PDF), is_admin=True)
        print("replace result:", json.dumps(res, default=str)[:1500])

    doc = (db.query(TargetBankDocument)
           .filter(TargetBankDocument.assessment_id == ASSESSMENT_ID,
                   TargetBankDocument.original_filename == os.path.basename(PDF))
           .order_by(TargetBankDocument.id.desc()).first())
    print("bank document", doc.id, json.loads(doc.stats_json or "{}"))
    items = db.query(TargetBankItem).filter(TargetBankItem.document_id == doc.id).all()
    print("before", collections.Counter(i.verification_status for i in items))

    auto = [i.id for i in items if i.verification_status == "auto_verified"]
    r = tbs.approve_items(auto, admin.id) if auto else {"approved": [], "skipped": []}
    print("bulk-approved auto_verified:", len(r["approved"]), "skipped:", r["skipped"])

    corrected, approved_review, left = [], [], []
    for i in sorted(items, key=lambda x: x.source_question_number):
        db.refresh(i)
        if i.verification_status != "needs_review":
            continue
        n = str(i.source_question_number)
        if n not in FIXES:
            left.append((n, json.loads(i.verification_report_json or "{}").get("blocking")))
            continue
        fix = FIXES[n]
        if fix:
            changes = {}
            if fix.get("stem"): changes["question_text"] = fix["stem"]
            if fix.get("options"): changes["options"] = fix["options"]
            tbs.correct_item(i.id, admin.id, changes)
            corrected.append(n)
        res = tbs.approve_items([i.id], admin.id, reviewed_individually=True)
        if res["approved"]:
            approved_review.append(n)
        else:
            left.append((n, res["skipped"]))
    items = db.query(TargetBankItem).filter(TargetBankItem.document_id == doc.id).all()
    print("corrected", len(corrected), "| approved after review", len(approved_review))
    print("NOT approved (need a human look):", left)
    print("after", collections.Counter(i.verification_status for i in items))
