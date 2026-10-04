/** Each application's route tree. An application appears here once it exists; the server decides
 *  separately whether it is released in this deployment (a page of an unreleased application
 *  finds no workspace to work in and says so). */
import type { ReactNode } from "react";
import { formsPublicRoutes, formsRoutes } from "./apps/forms";
import { agentRoutes } from "./apps/agent";
import { tablesRoutes } from "./apps/tables";
import { dataprepRoutes } from "./apps/dataprep";
import { extractRoutes } from "./apps/extract";
import { approvalsRoutes } from "./apps/approvals";

export const APP_ROUTES: ReactNode[] = [...formsRoutes(), ...agentRoutes(), ...tablesRoutes(), ...dataprepRoutes(), ...approvalsRoutes(), ...extractRoutes()];

/** Pages for people without an account (a form's fill-in link). Rendered outside the session and
 *  the catalogue (App.tsx): neither has anything to say to them, and asking only earns a 401. */
export const PUBLIC_ROUTES: ReactNode[] = [...formsPublicRoutes()];
