/** Each application's route tree. An application appears here once it exists; the server decides
 *  separately whether it is released in this deployment (a page of an unreleased application
 *  finds no workspace to work in and says so). */
import type { ReactNode } from "react";
import { formsRoutes } from "./apps/forms";
import { agentRoutes } from "./apps/agent";

export const APP_ROUTES: ReactNode[] = [...formsRoutes(), ...agentRoutes()];
