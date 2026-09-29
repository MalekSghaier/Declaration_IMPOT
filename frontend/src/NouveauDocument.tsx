import { useCallback, useEffect, useState } from "react";
import type { DragEvent } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { api } from "./api";
import "./NouveauDocument.css";

type Kind = "FACTURE" | "FICHE_PAIE";

type FileResult = {
  filename: string;
  status: "accepted" | "duplicate" | "rejected";
  document_id: number | null;
  reason: string | null;
};
type UploadOut = { accepted: number; duplicates: number; rejected: number; results: FileResult[] };
type Summary = { total: number; counts: Record<string, number> };
type Report = { accepted: number; duplicates: number; rejected: number; problems: FileResult[] };
type Piece = { id: number; filename: string; direction: string | null; status: string };

const BATCH_SIZE = 20;
const POLL_MS = 2000;
const MAX_PROBLEMS_SHOWN = 50;
const MAX_PIECES_SHOWN = 100;

const STATUS_LABELS: Record<string, string> = {
  UPLOADED: "En attente",
  PROCESSING: "En cours",
  EXTRACTED: "Extraite",
  NEEDS_REVIEW: "À relire",
  VALIDATED: "Validée",
  REJECTED: "Rejetée",
  FAILED: "Échec",
};

function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function errorMessage(e: unknown): string {
  if (axios.isAxiosError(e)) {
    const detail = e.response?.data?.detail;
    if (typeof detail === "string") return detail;
    if (!e.response) return "Serveur injoignable, reessayez.";
  }
  return "Une erreur est survenue pendant l'envoi.";
}

type ZoneProps = { kind: Kind; title: string; hint: string; period: string; processed: boolean };

function DropZone({ kind, title, hint, period, processed }: ZoneProps) {
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [sent, setSent] = useState(0);
  const [toSend, setToSend] = useState(0);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [pieces, setPieces] = useState<Piece[]>([]);

  const refresh = useCallback(async () => {
    if (!period) return;
    try {
      const { data } = await api.get<Summary>("/pieces/summary", { params: { period, kind } });
      setSummary(data);
    } catch {
      /* on reessaiera au prochain cycle */
    }
    if (processed) {
      try {
        const { data } = await api.get<{ items: Piece[] }>("/pieces", {
          params: { period, kind, limit: MAX_PIECES_SHOWN },
        });
        setPieces(data.items);
      } catch {
        /* idem */
      }
    }
  }, [period, kind, processed]);

  const count = (s: string) => summary?.counts[s] ?? 0;
  const pending = count("UPLOADED") + count("PROCESSING");

  useEffect(() => {
    setSummary(null);
    setPieces([]);
    void refresh();
  }, [refresh]);

  // Polling toutes les 2 s, uniquement tant qu'il y a du travail en cours
  useEffect(() => {
    if (!processed || (pending === 0 && !uploading)) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [processed, pending, uploading, refresh]);

  const upload = async (files: File[]) => {
    if (!files.length || uploading || !period) return;
    setUploading(true);
    setError(null);
    setReport(null);
    setSent(0);
    setToSend(files.length);

    const rep: Report = { accepted: 0, duplicates: 0, rejected: 0, problems: [] };
    try {
      for (let i = 0; i < files.length; i += BATCH_SIZE) {
        const form = new FormData();
        form.append("kind", kind);
        form.append("period", period);
        files.slice(i, i + BATCH_SIZE).forEach((f) => form.append("files", f));

        const { data } = await api.post<UploadOut>("/pieces/upload", form);
        rep.accepted += data.accepted;
        rep.duplicates += data.duplicates;
        rep.rejected += data.rejected;
        rep.problems.push(...data.results.filter((r) => r.status !== "accepted"));
        setSent(Math.min(i + BATCH_SIZE, files.length));
        void refresh();
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setReport({ ...rep });
      setUploading(false);
      void refresh();
    }
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    void upload(Array.from(e.dataTransfer.files));
  };

  const done = summary ? summary.total - pending : 0;
  const percent = summary && summary.total > 0 ? Math.round((done / summary.total) * 100) : 0;

  return (
    <section className="nd-card">
      <h2 className="nd-card-title">{title}</h2>

      <div
        className={`nd-drop${dragOver ? " nd-drop-over" : ""}${uploading ? " nd-drop-busy" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <p>Glissez vos fichiers ici</p>
        <p className="db-muted">{hint}</p>
        <label className="nd-btn">
          Choisir des fichiers
          <input
            type="file"
            multiple
            accept=".pdf,.png,.jpg,.jpeg"
            disabled={uploading}
            hidden
            onChange={(e) => {
              void upload(Array.from(e.target.files ?? []));
              e.target.value = "";
            }}
          />
        </label>
      </div>

      {uploading && (
        <div className="nd-block">
          <div className="nd-bar">
            <div className="nd-bar-fill" style={{ width: `${toSend ? (sent / toSend) * 100 : 0}%` }} />
          </div>
          <p className="db-muted">
            Envoi : {sent} / {toSend} fichiers
          </p>
        </div>
      )}

      {error && <p className="nd-error">{error}</p>}

      {report && (
        <div className="nd-block">
          <p>
            <strong>{report.accepted}</strong> accepté(s), <strong>{report.duplicates}</strong> doublon(s),{" "}
            <strong>{report.rejected}</strong> rejeté(s)
          </p>
          {report.problems.length > 0 && (
            <ul className="nd-problems">
              {report.problems.slice(0, MAX_PROBLEMS_SHOWN).map((p, i) => (
                <li key={`${p.filename}-${i}`}>
                  <span className={p.status === "rejected" ? "nd-tag-red" : "nd-tag-grey"}>
                    {p.status === "rejected" ? "Rejeté" : "Doublon"}
                  </span>{" "}
                  {p.filename} : {p.reason}
                </li>
              ))}
              {report.problems.length > MAX_PROBLEMS_SHOWN && (
                <li className="db-muted">… et {report.problems.length - MAX_PROBLEMS_SHOWN} autres</li>
              )}
            </ul>
          )}
        </div>
      )}

      <div className="nd-block">
        <p className="nd-sub">Période {period || "—"}</p>
        {!summary || summary.total === 0 ? (
          <p className="db-muted">Aucune pièce déposée pour ce mois.</p>
        ) : processed ? (
          <>
            <div className="nd-bar">
              <div className="nd-bar-fill nd-bar-green" style={{ width: `${percent}%` }} />
            </div>
            <p className="db-muted">
              Traitement : {done} / {summary.total} ({percent} %)
            </p>
            <ul className="nd-stats">
              <li>En attente : {pending}</li>
              <li>Extraites : {count("EXTRACTED")}</li>
              <li>À relire : {count("NEEDS_REVIEW")}</li>
              <li>Validées : {count("VALIDATED")}</li>
              <li>Échecs : {count("FAILED")}</li>
            </ul>

            {pieces.length > 0 && (
              <ul className="nd-pieces">
                {pieces.map((p) => (
                  <li key={p.id}>
                    <Link to={`/dashboard/nouveau-document/${p.id}`} className="nd-piece-link">
                      <span className={`nd-badge nd-badge-${p.status.toLowerCase()}`}>
                        {STATUS_LABELS[p.status] ?? p.status}
                      </span>
                      <span className="nd-piece-name">{p.filename}</span>
                      {p.direction && <span className="nd-piece-dir">{p.direction}</span>}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p>
            <strong>{summary.total}</strong> fiche(s) déposée(s). La lecture automatique des fiches de paie
            n'est pas encore active.
          </p>
        )}
      </div>
    </section>
  );
}

export default function NouveauDocument() {
  const [period, setPeriod] = useState(currentMonth());

  return (
    <>
      <h1 className="db-title">Nouveau document</h1>
      <p className="db-muted">
        Déposez les factures et les fiches de paie du mois. Elles sont lues automatiquement en arrière-plan.
      </p>

      <label className="nd-period">
        Mois concerné
        <input type="month" value={period} onChange={(e) => setPeriod(e.target.value)} />
      </label>

      <div className="nd-grid">
        <DropZone kind="FACTURE" title="Factures" hint="PDF, PNG ou JPG" period={period} processed />
        <DropZone
          kind="FICHE_PAIE"
          title="Fiches de paie"
          hint="PDF, PNG ou JPG"
          period={period}
          processed={false}
        />
      </div>
    </>
  );
}