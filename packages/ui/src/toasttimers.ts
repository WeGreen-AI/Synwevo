/** Auto-dismiss timers for the toast stack, with a pause that cannot get stuck.
 *
 *  Hovering or focusing the stack pauses every countdown: an error toast that vanishes while
 *  someone is still reading it is the one message they needed. The pause used to be a flag set
 *  by mouseenter/focus and cleared only by mouseleave/blur. A keyboard user who closed a toast
 *  with its ✕ removed the focused button from the page, and Firefox and Safari fire no blur for
 *  a removed element — so the flag stayed set, and every later toast, 8-second errors included,
 *  was created paused and stayed until someone happened to move the mouse over the corner.
 *
 *  Here the pause is re-read from the page (`held`) before a new toast starts counting and
 *  whenever the stack changes, so a hold that ended without an event still ends.
 *
 *  No imports: tested with `node --test` with a hand-driven clock.
 */
export interface Clock {
  set(fn: () => void, ms: number): unknown;
  clear(handle: unknown): void;
  now(): number;
}

const realClock: Clock = {
  set: (fn, ms) => setTimeout(fn, ms),
  clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>),
  now: () => Date.now(),
};

/** A resumed toast always gets at least this long, so it is still readable after the pause. */
export const RESUME_MIN_MS = 800;

export class ToastTimers {
  private timers = new Map<number, { h: unknown; left: number; start: number }>();
  private paused = false;

  /** `expire` removes the toast; `held` says whether the pointer or focus is on the stack now. */
  constructor(private expire: (id: number) => void, private held: () => boolean, private clock: Clock = realClock) {}

  get isPaused(): boolean {
    return this.paused;
  }

  /** Whether a countdown is running for this toast (false while paused or once gone). */
  running(id: number): boolean {
    const x = this.timers.get(id);
    return !!x && x.h != null;
  }

  /** Start the countdown for a new toast. The hold is re-read first: a pause whose end fired
   *  no event must not be inherited by every toast that comes after it. */
  arm(id: number, ms: number): void {
    this.recheck();
    this.timers.set(id, { h: this.paused ? null : this.start(id, ms), left: ms, start: this.clock.now() });
  }

  /** The toast was closed or dropped; its countdown goes with it. */
  forget(id: number): void {
    const x = this.timers.get(id);
    if (x && x.h != null) this.clock.clear(x.h);
    this.timers.delete(id);
  }

  /** Pointer or focus arrived on the stack. */
  hold(): void {
    if (this.paused) return;
    this.paused = true;
    const now = this.clock.now();
    for (const x of this.timers.values()) {
      if (x.h != null) this.clock.clear(x.h);
      x.left = Math.max(RESUME_MIN_MS, x.left - (now - x.start));
      x.h = null;
    }
  }

  /** Pointer and focus have both left the stack. */
  release(): void {
    if (!this.paused) return;
    this.paused = false;
    for (const [id, x] of this.timers) {
      x.start = this.clock.now();
      x.h = this.start(id, x.left);
    }
  }

  /** Re-read the hold from the page and resume if nothing holds the stack any more. Called after
   *  the stack changed: removing the focused ✕ is exactly the change that fires no blur. */
  recheck(): void {
    if (this.paused && !this.held()) this.release();
  }

  dispose(): void {
    for (const x of this.timers.values()) if (x.h != null) this.clock.clear(x.h);
    this.timers.clear();
  }

  private start(id: number, ms: number): unknown {
    return this.clock.set(() => {
      this.timers.delete(id);
      this.expire(id);
    }, ms);
  }
}
