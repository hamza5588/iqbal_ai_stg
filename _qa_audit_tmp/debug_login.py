#!/usr/bin/env python3
import requests

s = requests.Session()
r = s.post(
    "http://127.0.0.1:5000/auth/login",
    data={"useremail": "admin@iqbalai.com", "password": "Hamzakhanswati12@"},
    headers={"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"},
    timeout=60,
)
print("status", r.status_code)
print("body", r.text[:400])
print("set-cookie", r.headers.get("Set-Cookie"))
print("cookies", s.cookies.get_dict())
r2 = s.get("http://127.0.0.1:5000/admin/users", timeout=60)
print("users", r2.status_code, r2.text[:300])
r3 = s.get("http://127.0.0.1:5000/api/lms/admin/diagnostics", timeout=60)
print("diags", r3.status_code, r3.text[:300])
