"""Test du traitement automatique des factures. Temporaire : a supprimer apres validation."""
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE = "http://localhost:8000"
RUN = uuid.uuid4().hex[:8]
DONE = ("EXTRACTED", "NEEDS_REVIEW", "FAILED", "VALIDATED")
TIMEOUT_SECONDS = 240


def fake_pdf(label: str) -> bytes:
    return f"%PDF-1.4\n% {RUN} {label}\n%%EOF\n".encode()


def main(email: str, password: str, real_path: str | None) -> None:
    with httpx.Client(base_url=BASE, timeout=60) as c:
        r = c.post("/api/auth/login", json={"email": email, "password": password})
        r.raise_for_status()
        c.headers["Authorization"] = f"Bearer {r.json()['access_token']}"

        files = [("files", (f"test_proc_{i}.pdf", fake_pdf(str(i)), "application/pdf")) for i in range(3)]
        if real_path:
            p = Path(real_path)
            files.append(("files", (p.name, p.read_bytes(), "application/octet-stream")))

        r = c.post("/api/pieces/upload", data={"kind": "FACTURE", "period": "2025-09"}, files=files)
        r.raise_for_status()
        out = r.json()
        print(f"depot : {out['accepted']} acceptes, {out['duplicates']} doublons, {out['rejected']} rejetes")
        for item in out["results"]:
            if item["status"] == "rejected":
                print("   rejete :", item["filename"], "-", item["reason"])
        ids = [x["document_id"] for x in out["results"] if x["document_id"]]

        start = time.time()
        details = {}
        while time.time() - start < TIMEOUT_SECONDS:
            details = {i: c.get(f"/api/pieces/{i}").json() for i in ids}
            waiting = [i for i, d in details.items() if d["status"] not in DONE]
            print(f"   {int(time.time() - start):>3}s : {len(ids) - len(waiting)}/{len(ids)} traites")
            if not waiting:
                break
            time.sleep(2)

        print()
        for i, d in details.items():
            print(f"#{i} {d['filename']} -> {d['status']}")
            if d["error"]:
                print("    erreur :", d["error"])
            data = d["extracted_data"]
            if data:
                print("    methode :", data.get("method"))
                for issue in data.get("issues", []):
                    print(f"    [{issue['severity']}] {issue['field']} : {issue['message']}")
                if not d["filename"].startswith("test_"):
                    print(json.dumps(data["fields"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit("Usage : python try_process.py EMAIL MOT_DE_PASSE [chemin_vers_une_vraie_facture]")
    main(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else None)