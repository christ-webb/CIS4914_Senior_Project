import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;

HTMLElement.prototype.scrollIntoView = () => undefined;
Object.defineProperty(HTMLElement.prototype, "getBoundingClientRect", {
  configurable: true,
  value() {
    return { width: 800, height: 560, top: 0, left: 0, bottom: 560, right: 800, x: 0, y: 0, toJSON() {} };
  },
});

afterEach(() => cleanup());
