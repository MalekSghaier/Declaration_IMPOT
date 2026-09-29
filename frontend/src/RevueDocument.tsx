import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import axios from "axios";
import { api, fetchFileBlobUrl } from "./api";
import "./RevueDocument.css";

type TvaLine = { taux: number; base_ht: number; montant_tva: number };
type Fields = {
  numero: string | null;
  date_facture: string | null;
  emetteur_nom: string | null;
  emetteur_mf: string | null;
  client_nom: string | null;
  client_mf: string | null;
  lignes_tva: TvaLine[];
  total_ht: number | null;
  total_tva: number | null;
  timbre: number | null;
  total_ttc: number | null;
  devise: string | null;
};
type Issue = { field: string; message: string; severity: "error" | "warning" };
type PieceDetail = {
  id: number;
  filename: string;
  period: string | null;
  direction: string | null;
  status: string;
  extracted_data: { fields: Fields; issues: Issue[]; method: string | null } | null;
};

const emptyFields: Fields = {
  numero: null,
  date_facture: null,
  emetteur_nom: null,
  emetteur_mf: null,
  client_nom: null,
  client_mf: null,
  lignes_tva: [],
  total_ht: null,
  total_tva: null,
  timbre: null,
  total_ttc: null,
  devise: null,
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

export default function RevueDocument() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [piece, setPiece] = useState<PieceDetail | null>(null);
  const [fields, setFields] = useState<Fields>(emptyFields);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [direction, setDirection] = useState<"VENTE" | "ACHAT" | "">("");
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [isPdf, setIsPdf] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const { data } = await api.get<PieceDetail>(`/pieces/${id}`);
    setPiece(data);
    setFields(data.extracted_data?.fields ?? emptyFields);
    setIssues(data.extracted_data?.issues ?? []);
    setDirection((data.direction as "VENTE" | "ACHAT") ?? "");
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

  const setLine = (i: number, key: keyof TvaLine, value: number) =>
    setFields((f) => ({
      ...f,
      lignes_tva: f.lignes_tva.map((l, idx) => (idx === i ? { ...l, [key]: value } : l)),
    }));

  const addLine = () =>
    setFields((f) => ({ ...f, lignes_tva: [...f.lignes_tva, { taux: 19, base_ht: 0, montant_tva: 0 }] }));

  const removeLine = (i: number) =>
    setFields((f) => ({ ...f, lignes_tva: f.lignes_tva.filter((_, idx) => idx !== i) }));

  const submit = async (action: "save" | "validate" | "reject") => {
    setSaving(true);
    setError(null);
    try {
      const { data } = await api.put<PieceDetail>(`/pieces/${id}/review`, {
        fields,
        direction: direction || null,
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
          {locked && <p className="rv-locked">Cette pièce est {piece.status === "VALIDATED" ? "validée" : "rejetée"}, lecture seule.</p>}

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
            <label>
              Numéro
              <input value={fields.numero ?? ""} disabled={locked} onChange={(e) => setField("numero", e.target.value || null)} />
            </label>
            <label>
              Date facture
              <input
                type="date"
                value={fields.date_facture ?? ""}
                disabled={locked}
                onChange={(e) => setField("date_facture", e.target.value || null)}
              />
            </label>
          </div>

          <div className="rv-row">
            <label className={issueFor("emetteur_mf") ? "rv-warn" : ""}>
              Émetteur — nom
              <input value={fields.emetteur_nom ?? ""} disabled={locked} onChange={(e) => setField("emetteur_nom", e.target.value || null)} />
            </label>
            <label className={issueFor("emetteur_mf") ? "rv-warn" : ""}>
              Émetteur — matricule fiscal
              <input value={fields.emetteur_mf ?? ""} disabled={locked} onChange={(e) => setField("emetteur_mf", e.target.value || null)} />
            </label>
          </div>

          <div className="rv-row">
            <label>
              Client — nom
              <input value={fields.client_nom ?? ""} disabled={locked} onChange={(e) => setField("client_nom", e.target.value || null)} />
            </label>
            <label>
              Client — matricule fiscal
              <input value={fields.client_mf ?? ""} disabled={locked} onChange={(e) => setField("client_mf", e.target.value || null)} />
            </label>
          </div>

          <fieldset className="rv-lines">
            <legend>Lignes de TVA</legend>
            {fields.lignes_tva.map((l, i) => (
              <div className="rv-line" key={i}>
                <input type="number" value={l.taux} disabled={locked} onChange={(e) => setLine(i, "taux", num(e.target.value) ?? 0)} placeholder="Taux %" />
                <input type="number" value={l.base_ht} disabled={locked} onChange={(e) => setLine(i, "base_ht", num(e.target.value) ?? 0)} placeholder="Base HT" />
                <input type="number" value={l.montant_tva} disabled={locked} onChange={(e) => setLine(i, "montant_tva", num(e.target.value) ?? 0)} placeholder="Montant TVA" />
                {!locked && (
                  <button type="button" className="rv-remove" onClick={() => removeLine(i)}>
                    ✕
                  </button>
                )}
              </div>
            ))}
            {!locked && (
              <button type="button" className="rv-add" onClick={addLine}>
                + Ajouter une ligne
              </button>
            )}
          </fieldset>

          <div className="rv-row">
            <label className={issueFor("total_ttc") ? "rv-error" : ""}>
              Total HT
              <input type="number" value={fields.total_ht ?? ""} disabled={locked} onChange={(e) => setField("total_ht", num(e.target.value))} />
            </label>
            <label className={issueFor("total_tva") ? "rv-error" : ""}>
              Total TVA
              <input type="number" value={fields.total_tva ?? ""} disabled={locked} onChange={(e) => setField("total_tva", num(e.target.value))} />
            </label>
            <label>
              Timbre
              <input type="number" value={fields.timbre ?? ""} disabled={locked} onChange={(e) => setField("timbre", num(e.target.value))} />
            </label>
            <label className={issueFor("total_ttc") ? "rv-error" : ""}>
              Total TTC
              <input type="number" value={fields.total_ttc ?? ""} disabled={locked} onChange={(e) => setField("total_ttc", num(e.target.value))} />
            </label>
          </div>

          <div className="rv-row">
            <label className={issueFor("devise") ? "rv-warn" : ""}>
              Devise
              <input value={fields.devise ?? ""} disabled={locked} onChange={(e) => setField("devise", e.target.value || null)} />
            </label>
            <label className={issueFor("direction") ? "rv-warn" : ""}>
              Direction
              <select value={direction} disabled={locked} onChange={(e) => setDirection(e.target.value as "VENTE" | "ACHAT" | "")}>
                <option value="">— à choisir —</option>
                <option value="VENTE">Vente</option>
                <option value="ACHAT">Achat</option>
              </select>
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