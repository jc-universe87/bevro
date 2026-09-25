import { Navigate, Route, Routes, useSearchParams } from "react-router-dom";
import Shell from "./components/Shell";
import AppDetail from "./pages/AppDetail";
import Apps from "./pages/Apps";
import Automations from "./pages/Automations";
import Connect from "./pages/Connect";
import ConnectAdvanced from "./pages/ConnectAdvanced";
import Create from "./pages/Create";
import Home from "./pages/Home";
import Notifications from "./pages/Notifications";
import Recent from "./pages/Recent";
import Settings from "./pages/Settings";
import TaskPage from "./pages/TaskPage";

/** Links from before Apps & agents had pages of their own still arrive. */
function OldAgentsLink() {
  const [params] = useSearchParams();
  const id = params.get("manage");
  if (!id) return <Navigate to="/apps" replace />;
  const rest = new URLSearchParams();
  if (params.get("credential")) rest.set("credential", "1");
  if (params.get("test")) rest.set("test", "1");
  const query = rest.toString();
  return <Navigate to={`/apps/${id}${query ? `?${query}` : ""}`} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Home />} />
        <Route path="recent" element={<Recent />} />
        <Route path="scheduled" element={<Automations />} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="tasks/:id" element={<TaskPage />} />
        <Route path="apps" element={<Apps />} />
        <Route path="apps/:id" element={<AppDetail />} />
        <Route path="agents" element={<OldAgentsLink />} />
        <Route path="create" element={<Create />} />
        <Route path="connect" element={<Connect />} />
        <Route path="connect/advanced" element={<ConnectAdvanced />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
