import { Navigate, Route, Routes } from "react-router-dom";
import Shell from "./components/Shell";
import Agents from "./pages/Agents";
import Automations from "./pages/Automations";
import Connect from "./pages/Connect";
import ConnectAdvanced from "./pages/ConnectAdvanced";
import Create from "./pages/Create";
import Home from "./pages/Home";
import Notifications from "./pages/Notifications";
import Recent from "./pages/Recent";
import Settings from "./pages/Settings";
import TaskPage from "./pages/TaskPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Home />} />
        <Route path="recent" element={<Recent />} />
        <Route path="scheduled" element={<Automations />} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="tasks/:id" element={<TaskPage />} />
        <Route path="agents" element={<Agents />} />
        <Route path="create" element={<Create />} />
        <Route path="connect" element={<Connect />} />
        <Route path="connect/advanced" element={<ConnectAdvanced />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
