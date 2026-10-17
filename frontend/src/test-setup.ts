import "@testing-library/jest-dom/vitest";

// jsdom has no PointerEvent, so testing-library would drop clientX/clientY. A MouseEvent subclass keeps them.
if (typeof window.PointerEvent === "undefined") {
  class PointerEventPolyfill extends MouseEvent {
    pointerId: number;
    constructor(type: string, init: PointerEventInit = {}) {
      super(type, init);
      this.pointerId = init.pointerId ?? 1;
    }
  }
  // @ts-expect-error assigning the polyfill to the missing global
  window.PointerEvent = PointerEventPolyfill;
}
