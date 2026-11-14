/**
 * Test shim: installs the real page bus (static/js/uderiaBus.js) as the global `UderiaBus` so a
 * gate can import a renderer or handler that emits or listens through it. The bus resolves
 * `document` per call, so a gate may install its own document stub before OR after this import;
 * when none exists a minimal EventTarget-backed stub is provided.
 *
 *    */
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
if (typeof globalThis.window === 'undefined') globalThis.window = globalThis;
if (typeof globalThis.document === 'undefined') {
    const et = new EventTarget();
    globalThis.document = {
        addEventListener: (...a) => et.addEventListener(...a),
        removeEventListener: (...a) => et.removeEventListener(...a),
        dispatchEvent: (e) => et.dispatchEvent(e),
        getElementById: () => null,
        querySelector: () => null,
        querySelectorAll: () => [],
        createElement: () => ({ style: {}, classList: { add() {}, remove() {}, contains() { return false; } }, setAttribute() {}, appendChild() {} }),
        body: { appendChild() {} },
    };
}
if (typeof globalThis.CustomEvent === 'undefined') {
    globalThis.CustomEvent = class CustomEvent extends Event {
        constructor(type, init) { super(type, init); this.detail = init && init.detail; }
    };
}
if (!globalThis.window.UderiaBus) {
    const src = fs.readFileSync(path.join(here, '..', 'static', 'js', 'uderiaBus.js'), 'utf8');
    new Function(src)();
}
export const UderiaBus = globalThis.window.UderiaBus;
