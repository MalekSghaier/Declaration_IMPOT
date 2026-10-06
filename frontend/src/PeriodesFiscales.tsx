import { useCallback, useEffect, useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { api } from "./api";
import "./PeriodesFiscales.css";

type TaxPeriod = {
  id: number;
  period_type: "MONTH";
  period_start: string;
  period_end: string;
  status: string;
  document_count: number;
  editable: boolean;
  accepts_documents: boolean;
  available_transitions: string[];
  created_at: string;
  updated_at: string;
};

const STATUS_LABELS: Record<string, string> = {
  OPEN: "Ouverte",
  DOCUMENTS_IN_PROGRESS: "Saisie en cours",
  CALCULATED: "Calculée",
  READY_FOR_REVIEW: "Prête pour revue",
  VALIDATED: "Validée",
  FINALIZED: "Finalisée",
  ARCHIVED: "Archivée",
};

const TRANSITION_LABELS: Record<string, string> = {
  DOCUMENTS_IN_PROGRESS: "Démarrer la saisie des documents",
};

const MONTHS = [
  "Janvier",
  "Février",
  "Mars",
  "Avril",
  "Mai",
  "Juin",
  "Juillet",
  "Août",
  "Septembre",
  "Octobre",
  "Novembre",
  "Décembre",
];

const pad = (n: number) => String(n).padStart(2, "0");

function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
}

// Calcul des dates sans Date JavaScript
// pour éviter les problèmes de fuseau horaire.
function lastDay(year: number, month: number): number {
  if (month === 2) {
    return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
      ? 29
      : 28;
  }

  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

function buildMonthDates(month: string) {
  const match = /^([0-9]{4})-(0[1-9]|1[0-2])$/.exec(month);

  if (!match || Number(match[1]) < 1) {
    return null;
  }

  const year = Number(match[1]);
  const monthNumber = Number(match[2]);
  const last = lastDay(year, monthNumber);

  return {
    start: `${match[1]}-${match[2]}-01`,
    end: `${match[1]}-${match[2]}-${pad(last)}`,
  };
}

function parts(iso: string) {
  const [y, m, d] = iso.split("-").map(Number);
  return { y, m, d };
}

function fmt(iso: string): string {
  const { y, m, d } = parts(iso);
  return `${pad(d)}/${pad(m)}/${y}`;
}

function periodLabel(period: TaxPeriod): string {
  const { y, m } = parts(period.period_start);
  return `${MONTHS[m - 1]} ${y}`;
}

function errorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;

    if (typeof detail === "string") {
      return detail.includes(" : ")
        ? detail.split(" : ").slice(1).join(" : ")
        : detail;
    }

    if (!error.response) {
      return "Serveur injoignable, réessayez.";
    }
  }

  return "Une erreur est survenue.";
}

export default function PeriodesFiscales() {
  const [periods, setPeriods] = useState<TaxPeriod[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [month, setMonth] = useState(currentMonth());
  const [creating, setCreating] = useState(false);

  const [openId, setOpenId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<TaxPeriod[]>("/tax-periods");

      // L'application travaille actuellement uniquement
      // avec les périodes mensuelles.
      setPeriods(data.filter((period) => period.period_type === "MONTH"));

      setLoadError(null);
    } catch (error) {
      setLoadError(errorMessage(error));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async (event: FormEvent) => {
    event.preventDefault();

    const dates = buildMonthDates(month);

    if (!dates) {
      setActionError("Mois invalide.");
      return;
    }

    setCreating(true);
    setActionError(null);

    try {
      await api.post("/tax-periods", {
        period_type: "MONTH",
        period_start: dates.start,
        period_end: dates.end,
      });

      await load();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setCreating(false);
    }
  };

  const changeStatus = async (
    period: TaxPeriod,
    status: string,
  ) => {
    setBusyId(period.id);
    setActionError(null);

    try {
      await api.patch(`/tax-periods/${period.id}`, {
        status,
      });

      await load();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <>
      <h1 className="db-title">Périodes fiscales</h1>

      <p className="db-muted">
        Chaque période fiscale correspond à un mois. Elle regroupe les
        documents et les données utilisés pour préparer la déclaration
        mensuelle de l'entreprise.
      </p>

      <section className="db-card tp-card">
        <h2 className="tp-subtitle">Nouvelle période mensuelle</h2>

        <form
          className="tp-form"
          onSubmit={(event) => void create(event)}
        >
          <label className="tp-field">
            Mois
            <input
              type="month"
              value={month}
              onChange={(event) => setMonth(event.target.value)}
              required
            />
          </label>

          <button
            className="tp-btn"
            type="submit"
            disabled={creating}
          >
            {creating ? "Création…" : "Créer"}
          </button>
        </form>

        <p className="tp-hint">
          Les dates de début et de fin sont automatiquement calculées
          pour le mois sélectionné.
        </p>

        {actionError && (
          <p className="db-error">{actionError}</p>
        )}
      </section>

      <section className="db-card tp-card">
        <h2 className="tp-subtitle">Mes périodes mensuelles</h2>

        {loadError && (
          <p className="db-error">{loadError}</p>
        )}

        {!periods && !loadError && (
          <p className="db-muted">Chargement…</p>
        )}

        {periods && periods.length === 0 && (
          <p className="db-muted">
            Aucune période fiscale pour le moment.
          </p>
        )}

        {periods && periods.length > 0 && (
          <ul className="tp-list">
            {periods.map((period) => {
              const open = openId === period.id;
              const count = period.document_count;

              return (
                <li
                  key={period.id}
                  className="tp-item"
                >
                  <button
                    type="button"
                    className="tp-row"
                    aria-expanded={open}
                    onClick={() =>
                      setOpenId(open ? null : period.id)
                    }
                  >
                    <span className="tp-name">
                      {periodLabel(period)}
                    </span>

                    <span className="tp-badge tp-badge-month">
                      Mensuelle
                    </span>

                    <span
                      className={`tp-badge tp-badge-${period.status.toLowerCase()}`}
                    >
                      {STATUS_LABELS[period.status] ??
                        period.status}
                    </span>

                    <span className="tp-count">
                      {count} document
                      {count > 1 ? "s" : ""}
                    </span>
                  </button>

                  {open && (
                    <div className="tp-detail">
                      <p className="tp-dates">
                        Du {fmt(period.period_start)} au{" "}
                        {fmt(period.period_end)}
                      </p>

                      <div className="tp-actions">
                        {period.accepts_documents && (
                          <Link
                            className="tp-btn tp-btn-link"
                            to={`/dashboard/nouveau-document?period=${period.period_start.slice(
                              0,
                              7,
                            )}`}
                          >
                            Déposer des documents
                          </Link>
                        )}

                        {period.available_transitions.map(
                          (transition) => (
                            <button
                              key={transition}
                              type="button"
                              className="tp-btn tp-btn-ghost"
                              disabled={busyId === period.id}
                              onClick={() =>
                                void changeStatus(
                                  period,
                                  transition,
                                )
                              }
                            >
                              {TRANSITION_LABELS[
                                transition
                              ] ??
                                `Passer à « ${
                                  STATUS_LABELS[transition] ??
                                  transition
                                } »`}
                            </button>
                          ),
                        )}
                      </div>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </>
  );
}