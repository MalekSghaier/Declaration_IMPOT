import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import axios from "axios";
import { api, fetchFileBlobUrl } from "./api";
import "./RevueDocument.css";   // reutilise le style existant

type Fields = {
  salarie_nom: string | null;
  mois: string | null;
  salaire_brut: number | null;
  cnss_salariale: number | null;
  salaire_imposable: number | null;
  retenue_irpp: number | null;
  css: number | null;
  net_a_payer: number | null;
};

type Issue = { field: string; message: string; severity: "error" | "warning" };

type PieceDetail = {
  id: number;
  filename: string;
  kind: string;
  period: string | null;
  status: string;
  extracted_data: { fields: Fields; issues: Issue[]; method: string | null } | null;
};

const emptyFields: Fields = {
  salarie_nom: null,
  mois: null,
  salaire_brut: null,
  cnss_salariale: null,
  salaire_imposable: null,
  retenue_irpp: null,
  css: null,
  net_a_payer: null,
};

function num(v: string): number | null {
  if (v.trim() === "") return null;
  const n = Number(v);
  return Number.isNaN(n) ? null : n;
}

function errorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = e.response?.data?.detail;
    if (typeof detail === "string") return detail;
  }
  return "Une erreur est survenue.";
}

export default function RevueFichePaie() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [piece, setPiece] = useState<PieceDetail | null>(null);
  const [fields, setFields] = useState<Fields>(emptyFields);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [isPdf, setIsPdf] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data } = await api.get<PieceDetail>(`/pieces/${id}`);
    setPiece(data);
    setFields(data.extracted_data?.fields ?? emptyFields);
    setIssues(data.extracted_data?.issues ?? []);
    setIsPdf(data.filename.toLowerCase().endsWith(".pdf"));
    const url = await fetchFileBlobUrl(`/pieces/${id}/file`);
    setFileUrl(url);
  }, [id]);

  useEffect(() => {
    void load();
    return () => {
      if (fileUrl) URL.revokeObjectURL(fileUrl);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load]);

  const setField = <K extends keyof Fields>(key: K, value: Fields[K]) =>
    setFields((f) => ({ ...f, [key]: value }));

  const submit = async (action: "save" | "validate" | "reject") => {
    setSaving(true);
    setError(null);
    try {
      const { data } = await api.put<PieceDetail>(`/pieces/${id}/review`, {
        fields,
        direction: null,
        action,
      });
      setPiece(data);
      setIssues(data.extracted_data?.issues ?? []);
      if (action !== "save") navigate("/dashboard/nouveau-document");
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setSaving(false);
    }
  };

  if (!piece) return <p className="db-muted">Chargement…</p>;

  const locked = piece.status === "VALIDATED" || piece.status === "REJECTED";
  const issueFor = (field: string) => issues.find((i) => i.field === field);

  return (
    <>
      <h1 className="db-title">Relecture — {piece.filename}</h1>
      <Link to="/dashboard/nouveau-document" className="rv-back">← Retour</Link>

      <div className="rv-grid">
        <div className="rv-preview">
          {fileUrl && (isPdf ? <iframe src={fileUrl} title="document" /> : <img src={fileUrl} alt="document" />)}
        </div>

        <div className="rv-form">
          {locked && (
            <p className="rv-locked">
              Cette pièce est {piece.status === "VALIDATED" ? "validée" : "rejetée"}, lecture seule.
            </p>
          )}

          {issues.length > 0 && (
            <ul className="rv-issues">
              {issues.map((i, idx) => (
                <li key={idx} className={i.severity === "error" ? "rv-issue-error" : "rv-issue-warning"}>
                  {i.message}
                </li>
              ))}
            </ul>
          )}

          <div className="rv-row">
            <label className={issueFor("salarie_nom") ? "rv-warn" : ""}>
              Nom du salarié
              <input
                value={fields.salarie_nom ?? ""}
                disabled={locked}
                onChange={(e) => setField("salarie_nom", e.target.value || null)}
              />
            </label>
            <label className={issueFor("mois") ? "rv-warn" : ""}>
              Mois (AAAA-MM)
              <input
                value={fields.mois ?? ""}
                disabled={locked}
                placeholder="2025-09"
                onChange={(e) => setField("mois", e.target.value || null)}
              />
            </label>
          </div>

          <div className="rv-row">
            <label className={issueFor("salaire_brut") ? "rv-error" : ""}>
              Salaire brut
              <input type="number" value={fields.salaire_brut ?? ""} disabled={locked}
                onChange={(e) => setField("salaire_brut", num(e.target.value))} />
            </label>
            <label className={issueFor("cnss_salariale") ? "rv-error" : ""}>
              CNSS salariale
              <input type="number" value={fields.cnss_salariale ?? ""} disabled={locked}
                onChange={(e) => setField("cnss_salariale", num(e.target.value))} />
            </label>
          </div>

          <div className="rv-row">
            <label className={issueFor("salaire_imposable") ? "rv-error" : ""}>
              Salaire imposable
              <input type="number" value={fields.salaire_imposable ?? ""} disabled={locked}
                onChange={(e) => setField("salaire_imposable", num(e.target.value))} />
            </label>
            <label className={issueFor("retenue_irpp") ? "rv-error" : ""}>
              Retenue IRPP
              <input type="number" value={fields.retenue_irpp ?? ""} disabled={locked}
                onChange={(e) => setField("retenue_irpp", num(e.target.value))} />
            </label>
          </div>

          <div className="rv-row">
            <label>
              CSS
              <input type="number" value={fields.css ?? ""} disabled={locked}
                onChange={(e) => setField("css", num(e.target.value))} />
            </label>
            <label className={issueFor("net_a_payer") ? "rv-error" : ""}>
              Net à payer
              <input type="number" value={fields.net_a_payer ?? ""} disabled={locked}
                onChange={(e) => setField("net_a_payer", num(e.target.value))} />
            </label>
          </div>

          {error && <p className="rv-error-msg">{error}</p>}

          {!locked && (
            <div className="rv-actions">
              <button disabled={saving} onClick={() => submit("save")}>Enregistrer</button>
              <button disabled={saving} className="rv-btn-primary" onClick={() => submit("validate")}>Valider</button>
              <button disabled={saving} className="rv-btn-danger" onClick={() => submit("reject")}>Rejeter</button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}