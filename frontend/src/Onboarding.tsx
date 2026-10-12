import { useState, type FormEvent } from "react";
import { isAxiosError } from "axios";
import { api, fetchFileBlobUrl } from "./api";
import { useAuth } from "./AuthContext";
import "./Onboarding.css";

interface Profile {
  raison_sociale: string | null;
  matricule_fiscal: string | null;
  adresse: string | null;
  forme_juridique: string | null;
  capital: number | null;
  date_creation: string | null;
  activite: string | null;
  dirigeant: string | null;
  code_tva: string | null;       
  code_categorie: string | null;
}

const EMPTY: Profile = {
  raison_sociale: null,
  matricule_fiscal: null,
  adresse: null,
  forme_juridique: null,
  capital: null,
  date_creation: null,
  activite: null,
  dirigeant: null,
  code_tva: null,
  code_categorie: null,
};

type Phase = "select" | "loading" | "review";
const MAX_FILE_BYTES = 50 * 1024 * 1024;
const isDocx = (f: File | null) => !!f && f.name.toLowerCase().endsWith(".docx");

const IconFile = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <polyline points="14 2 14 8 20 8" />
  </svg>
);

const IconCheck = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

const IconInfo = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="12" cy="12" r="10" />
    <line x1="12" y1="16" x2="12" y2="12" />
    <line x1="12" y1="8" x2="12.01" y2="8" />
  </svg>
);


function FileDrop({
  label,
  file,
  onFile,
}: {
  label: string;
  file: File | null;
  onFile: (f: File | null) => void;
}) {
  return (
    <label className={`ob-drop${file ? " has-file" : ""}`}>
      <input
        type="file"
        accept=".pdf,.png,.jpg,.jpeg,.docx"
        onChange={(e) => onFile(e.target.files?.[0] ?? null)}
        required
      />
      <span className="ob-drop-label">{label}</span>
      <span className="ob-drop-title">
        <IconFile />
        {file ? "Fichier sélectionné" : "Choisir un fichier"}
      </span>
      {file ? (
        <span className="ob-drop-filename">{file.name}</span>
      ) : (
        <span className="ob-drop-hint">PDF, PNG, JPG ou DOCX — glissez ou cliquez . 50 Mo max</span>
      )}
    </label>
  );
}

export default function Onboarding() {
  const { refreshUser } = useAuth();
  const [phase, setPhase] = useState<Phase>("select");
  const [patenteFile, setPatenteFile] = useState<File | null>(null);
  const [rneFile, setRneFile] = useState<File | null>(null);
  const [profile, setProfile] = useState<Profile>(EMPTY);
  const [previews, setPreviews] = useState<{ patente?: string; rne?: string }>({});
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);

  async function onUpload(e: FormEvent) {
    e.preventDefault();
    if (!patenteFile || !rneFile) {
      setError("Les deux fichiers sont requis.");
      return;
    }
    if (patenteFile.size > MAX_FILE_BYTES || rneFile.size > MAX_FILE_BYTES) {
      setError("Chaque fichier doit faire 50 Mo maximum.");
      return;
    }
    setError("");
    setPhase("loading");
    try {
      const form = new FormData();
      form.append("patente", patenteFile);
      form.append("rne", rneFile);
      const { data } = await api.post<Profile>("/onboarding/upload", form);
      setProfile(data);

      const [patenteUrl, rneUrl] = await Promise.all([
        fetchFileBlobUrl("/onboarding/document/PATENTE/file"),
        fetchFileBlobUrl("/onboarding/document/RNE/file"),
      ]);
      setPreviews({ patente: patenteUrl, rne: rneUrl });
      setPhase("review");
    } catch (err) {
      const detail = isAxiosError(err) ? err.response?.data?.detail : null;
      setError(typeof detail === "string" ? detail : "Erreur lors de l'extraction. Réessayez.");
      setPhase("select");
    }
  }

  async function onConfirm() {
    if (!profile.raison_sociale || !profile.matricule_fiscal) {
      setError("Raison sociale et matricule fiscal sont obligatoires.");
      return;
    }
    setError("");
    setConfirming(true);
    try {
      await api.post("/onboarding/confirm", profile);
      await refreshUser();
    } catch (err) {
      const detail = isAxiosError(err) ? err.response?.data?.detail : null;
      setError(typeof detail === "string" ? detail : "Erreur lors de la confirmation.");
    } finally {
      setConfirming(false);
    }
  }

  function field(key: keyof Profile, label: string, type: "text" | "number" | "date" = "text") {
    return (
      <div className="ob-field">
        <label htmlFor={`f-${key}`}>{label}</label>
        <div className="ob-input-wrap">
          <input
            id={`f-${key}`}
            type={type}
            value={profile[key] ?? ""}
            onChange={(e) =>
              setProfile((p) => ({
                ...p,
                [key]:
                  e.target.value === ""
                    ? null
                    : type === "number"
                    ? Number(e.target.value)
                    : e.target.value,
              }))
            }
          />
        </div>
      </div>
    );
  }

  return (
    <div className="ob-screen">
      <header className="ob-header">
        <span className="ob-brand">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
            <rect x="1" y="1" width="12" height="12" stroke="currentColor" strokeWidth="1.2" />
            <rect x="5" y="5" width="4" height="4" fill="#14b8a6" />
          </svg>
          Déclaration TVA
        </span>
        <span className="ob-step">
          {phase === "select" && "Étape 1 sur 2 — Documents"}
          {phase === "loading" && "Extraction en cours"}
          {phase === "review" && "Étape 2 sur 2 — Vérification"}
        </span>
      </header>

      {phase === "select" && (
        <div className="ob-card ob-select">
          <h1 className="ob-title">Informations de la société</h1>
          <p className="ob-subtitle">
            Uploadez la patente et le RNE pour pré-remplir votre profil. Vous pourrez tout
            vérifier à l'étape suivante.
          </p>
          <form onSubmit={onUpload}>
            <div className="ob-upload-grid">
              <FileDrop label="Patente" file={patenteFile} onFile={setPatenteFile} />
              <FileDrop label="RNE" file={rneFile} onFile={setRneFile} />
            </div>
            {error && <p className="ob-error">{error}</p>}
            <button type="submit" className="ob-btn">
              Extraire les informations
            </button>
          </form>
        </div>
      )}

      {phase === "loading" && (
        <div className="ob-card ob-loading">
          <div className="ob-spinner" />
          <p>Lecture des documents en cours…</p>
          <small>Extraction des champs via OCR — quelques secondes.</small>
        </div>
      )}

      {phase === "review" && (
        <div className="ob-review">
          <div className="ob-card">
            <h2 className="ob-card-title">
              <IconFile /> Aperçus
            </h2>
            {previews.patente && (
              <div className="ob-preview">
                <span className="ob-preview-label">Patente</span>
                {isDocx(patenteFile) ? (
                  <p className="ob-drop-hint">
                    Aperçu indisponible pour un fichier Word.{" "}
                    <a href={previews.patente} download={patenteFile?.name}>Télécharger</a>
                  </p>
                ) : (
                  <iframe src={previews.patente} title="patente" />
                )}
              </div>
            )}
            {previews.rne && (
              <div className="ob-preview">
                <span className="ob-preview-label">RNE</span>
                {isDocx(rneFile) ? (
                  <p className="ob-drop-hint">
                    Aperçu indisponible pour un fichier Word.{" "}
                    <a href={previews.rne} download={rneFile?.name}>Télécharger</a>
                  </p>
                ) : (
                  <iframe src={previews.rne} title="rne" />
                )}
              </div>
            )}

          </div>

          <div className="ob-card">
            <h2 className="ob-card-title">
              <IconCheck /> Vérifiez et corrigez
            </h2>
            <div className="ob-fields">
              {field("raison_sociale", "Raison sociale")}
              {field("matricule_fiscal", "Matricule fiscal")}
              {field("adresse", "Adresse")}
              {field("forme_juridique", "Forme juridique")}
              {field("capital", "Capital (DT)", "number")}
              {field("date_creation", "Date de création", "date")}
              {field("activite", "Activité")}
              {field("dirigeant", "Dirigeant")}
              {field("code_tva", "Code TVA")}
              {field("code_categorie", "Code catégorie")}
            </div>

            {error && <p className="ob-error">{error}</p>}

            <div className="ob-notice">
              <IconInfo />
              <span>
                Une fois confirmées, ces informations ne seront plus modifiables.
              </span>
            </div>

            <button onClick={onConfirm} disabled={confirming} className="ob-btn">
              {confirming ? "Confirmation…" : "Confirmer"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}