// Helpers for cross-platform keyboard modifier handling.
//
// On macOS, Ctrl+click is intercepted by the OS as a context-menu (right-click)
// gesture, so any "hold Ctrl while clicking" affordance is unreachable for Mac
// users. Treat the Cmd (Meta) key as the equivalent primary modifier so the
// same interactions work on every platform.

export const isMac = typeof navigator !== 'undefined'
    && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || '');

// True when the user is holding the platform's primary modifier (Ctrl on
// Windows/Linux, Cmd on macOS). Accept both on every platform so external
// keyboards keep working.
export function isPrimaryModifier(event) {
    return !!event && (event.ctrlKey || event.metaKey);
}

// Human-readable label for the primary modifier, for use in tooltips.
export const primaryModifierLabel = isMac ? 'Cmd' : 'Ctrl';
