/**
 * jsdom has no canvas backend, so `getContext("2d")` throws a "not implemented"
 * error that pollutes stderr and fails the PowerShell check script. A no-op
 * context lets the real render loop run end to end instead.
 */
const noop = () => undefined;

function createContext(canvas: HTMLCanvasElement): CanvasRenderingContext2D {
  const gradient = { addColorStop: noop } as unknown as CanvasGradient;
  const target: Record<string, unknown> = {
    canvas,
    createLinearGradient: () => gradient,
    createRadialGradient: () => gradient,
    measureText: () => ({ width: 0 }),
    getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    setTransform: noop,
    save: noop,
    restore: noop,
  };
  return new Proxy(target, {
    get(obj, prop) {
      if (prop in obj) return obj[prop as string];
      return noop;
    },
    set(obj, prop, value) {
      obj[prop as string] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
}

HTMLCanvasElement.prototype.getContext = function getContext(this: HTMLCanvasElement) {
  return createContext(this);
} as unknown as HTMLCanvasElement["getContext"];

// jsdom performs no layout, so a canvas would report a 0×0 client box and the
// render loop would bail out before drawing anything.
Object.defineProperty(HTMLCanvasElement.prototype, "clientWidth", { get: () => 800 });
Object.defineProperty(HTMLCanvasElement.prototype, "clientHeight", { get: () => 566 });
