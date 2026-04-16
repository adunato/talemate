export const FRONTEND_VERSION = "0.37.0.dev5";

export function versionsMatch(backendVersion) {
  return FRONTEND_VERSION === backendVersion;
}
