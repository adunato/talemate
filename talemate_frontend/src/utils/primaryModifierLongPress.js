const DEFAULT_LONG_PRESS_DELAY = 550;
const MOVE_TOLERANCE_PX = 12;
const SUPPRESS_CLICK_MS = 700;
const STYLE_ID = 'primary-modifier-long-press-style';
const STYLE_CLASS = 'primary-modifier-long-press';

function ensureStyle() {
    if (typeof document === 'undefined' || document.getElementById(STYLE_ID)) {
        return;
    }

    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = `
        .${STYLE_CLASS},
        .${STYLE_CLASS} * {
            -webkit-touch-callout: none;
            -webkit-user-select: none;
            user-select: none;
        }

        .${STYLE_CLASS} {
            touch-action: manipulation;
        }
    `;
    document.head.appendChild(style);
}

function isDisabled(el) {
    return el.matches?.('[disabled], [aria-disabled="true"], .v-btn--disabled, .v-list-item--disabled');
}

function getPoint(event) {
    const touch = event.changedTouches?.[0] || event.touches?.[0];
    return touch || event;
}

function dispatchPrimaryModifierClick(el, sourceEvent) {
    const point = getPoint(sourceEvent);
    const click = new MouseEvent('click', {
        bubbles: true,
        cancelable: true,
        composed: true,
        view: window,
        ctrlKey: true,
        clientX: point.clientX,
        clientY: point.clientY,
        screenX: point.screenX,
        screenY: point.screenY,
        button: 0,
        buttons: 0,
    });

    Object.defineProperty(click, 'longPressPrimaryModifier', {
        value: true,
        enumerable: true,
    });

    el.dispatchEvent(click);
}

function clearState(state) {
    if (state.timer) {
        window.clearTimeout(state.timer);
    }
    state.timer = null;
    state.pointerId = null;
    state.startX = 0;
    state.startY = 0;
}

function bind(el, binding) {
    ensureStyle();

    const delay = Number(binding.value?.delay || DEFAULT_LONG_PRESS_DELAY);
    const state = {
        timer: null,
        pointerId: null,
        startX: 0,
        startY: 0,
        suppressClickUntil: 0,
        triggered: false,
    };

    const cancel = () => clearState(state);

    const start = (event, point, pointerId = null) => {
        if (isDisabled(el)) {
            return;
        }

        clearState(state);
        state.pointerId = pointerId;
        state.startX = point.clientX;
        state.startY = point.clientY;
        state.triggered = false;

        state.timer = window.setTimeout(() => {
            state.triggered = true;
            state.suppressClickUntil = Date.now() + SUPPRESS_CLICK_MS;
            if (event.cancelable) {
                event.preventDefault();
            }
            dispatchPrimaryModifierClick(el, event);
            clearState(state);
        }, delay);
    };

    const onPointerDown = (event) => {
        if (event.pointerType === 'mouse' || event.button !== 0) {
            return;
        }

        start(event, event, event.pointerId);
    };

    const onPointerMove = (event) => {
        if (event.pointerId !== state.pointerId) {
            return;
        }

        const distance = Math.hypot(event.clientX - state.startX, event.clientY - state.startY);
        if (distance > MOVE_TOLERANCE_PX) {
            clearState(state);
        }
    };

    const onTouchStart = (event) => {
        if (event.touches.length !== 1) {
            return;
        }

        start(event, event.touches[0]);
    };

    const onTouchMove = (event) => {
        if (!state.timer || event.touches.length !== 1) {
            return;
        }

        const touch = event.touches[0];
        const distance = Math.hypot(touch.clientX - state.startX, touch.clientY - state.startY);
        if (distance > MOVE_TOLERANCE_PX) {
            clearState(state);
        }
    };

    const onClick = (event) => {
        if (event.longPressPrimaryModifier) {
            return;
        }

        if (Date.now() < state.suppressClickUntil) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    };

    const onContextMenu = (event) => {
        if (Date.now() < state.suppressClickUntil || state.timer) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    };

    const onSelectStart = (event) => {
        if (state.timer) {
            event.preventDefault();
            event.stopImmediatePropagation();
        }
    };

    el.__primaryModifierLongPress = {
        onPointerDown,
        onPointerMove,
        onPointerUp: cancel,
        onPointerCancel: cancel,
        onPointerLeave: cancel,
        onTouchStart,
        onTouchMove,
        onTouchEnd: cancel,
        onTouchCancel: cancel,
        onClick,
        onContextMenu,
        onSelectStart,
    };

    el.classList.add(STYLE_CLASS);
    el.addEventListener('pointerdown', onPointerDown);
    el.addEventListener('pointermove', onPointerMove);
    el.addEventListener('pointerup', cancel);
    el.addEventListener('pointercancel', cancel);
    el.addEventListener('pointerleave', cancel);
    el.addEventListener('touchstart', onTouchStart, { passive: false });
    el.addEventListener('touchmove', onTouchMove, { passive: false });
    el.addEventListener('touchend', cancel);
    el.addEventListener('touchcancel', cancel);
    el.addEventListener('click', onClick, true);
    el.addEventListener('contextmenu', onContextMenu, true);
    el.addEventListener('selectstart', onSelectStart, true);
}

function unbind(el) {
    const handlers = el.__primaryModifierLongPress;
    if (!handlers) {
        return;
    }

    el.classList.remove(STYLE_CLASS);
    el.removeEventListener('pointerdown', handlers.onPointerDown);
    el.removeEventListener('pointermove', handlers.onPointerMove);
    el.removeEventListener('pointerup', handlers.onPointerUp);
    el.removeEventListener('pointercancel', handlers.onPointerCancel);
    el.removeEventListener('pointerleave', handlers.onPointerLeave);
    el.removeEventListener('touchstart', handlers.onTouchStart);
    el.removeEventListener('touchmove', handlers.onTouchMove);
    el.removeEventListener('touchend', handlers.onTouchEnd);
    el.removeEventListener('touchcancel', handlers.onTouchCancel);
    el.removeEventListener('click', handlers.onClick, true);
    el.removeEventListener('contextmenu', handlers.onContextMenu, true);
    el.removeEventListener('selectstart', handlers.onSelectStart, true);
    delete el.__primaryModifierLongPress;
}

export default {
    mounted: bind,
    updated(el, binding) {
        if (binding.value?.delay === binding.oldValue?.delay) {
            return;
        }
        unbind(el);
        bind(el, binding);
    },
    unmounted: unbind,
};
