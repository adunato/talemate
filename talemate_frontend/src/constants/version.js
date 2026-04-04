export const FRONTEND_VERSION = "0.37.0.dev4";

export function versionsMatch(backendVersion) {
  return FRONTEND_VERSION === backendVersion;
}
