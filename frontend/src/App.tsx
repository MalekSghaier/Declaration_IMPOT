import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./AuthContext";
import DashboardLayout from "./DashboardLayout";
import Home from "./Home";
import Info from "./Info";
import Login from "./Login";
import MesTravaux from "./MesTravaux";
import NouveauDocument from "./NouveauDocument";
import Onboarding from "./Onboarding";
import ProtectedRoute from "./ProtectedRoute";
import RequireStatus from "./RequireStatus";
import Accueil from "./Accueil";
import RevueDocument from "./RevueDocument";
import RevueFichePaie from "./RevueFichePaie";
import PeriodesFiscales from "./PeriodesFiscales";


export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />

          <Route element={<ProtectedRoute />}>
            <Route path="/" element={<Home />} />

            {/* Societe non verrouillee : onboarding uniquement */}
            <Route element={<RequireStatus status="ONBOARDING" redirectTo="/dashboard" />}>
              <Route path="/onboarding" element={<Onboarding />} />
            </Route>

            {/* Societe verrouillee : dashboard uniquement */}
            <Route element={<RequireStatus status="LOCKED" redirectTo="/onboarding" />}>
              <Route path="/dashboard" element={<DashboardLayout />}>
                <Route index element={<Accueil />} />
                <Route path="info" element={<Info />} />
                <Route path="nouveau-document" element={<NouveauDocument />} />
                <Route path="nouveau-document/:id" element={<RevueDocument />} />
                <Route path="mes-travaux" element={<MesTravaux />} />
                <Route path="/dashboard/fiche-paie/:id" element={<RevueFichePaie />} />
                <Route path="periodes" element={<PeriodesFiscales />} />

              </Route>
            </Route>

            <Route path="*" element={<Navigate to="/" replace />} />
            
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}