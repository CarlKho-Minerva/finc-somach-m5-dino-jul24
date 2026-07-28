import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import DriveDashboard from "./drive/DriveDashboard";
import "./styles.css";

const root = document.getElementById("root");
if (!root) throw new Error("Dashboard root element was not found");

const parameters = new URLSearchParams(window.location.search);
const driveMode = window.location.pathname.replace(/\/$/, "") === "/drive" || parameters.get("mode") === "drive";

createRoot(root).render(
  <StrictMode>
    {driveMode ? <DriveDashboard /> : <App />}
  </StrictMode>,
);
