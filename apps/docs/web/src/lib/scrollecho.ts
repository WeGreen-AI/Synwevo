/** The page number a scrolling view reported to its parent, recognised when it comes back.
 *
 *  A paged view reports the page in view as the user scrolls (onPage), and scrolls to the page it
 *  is given (the page prop). When the parent hands the reported number back, the view must not
 *  scroll again: on short pages (landscape, zoomed out) that snap moved the next page past the
 *  threshold and cascaded to the end of the document.
 *
 *  The echo is recognised once. It used to be kept after use, so a later jump to that same page
 *  — a typed region on page 3 after a jump to page 8 that scrolled instantly and reported
 *  nothing — was taken for the echo and skipped: the page state said 3, the view stayed on 8.
 *
 *  No imports: tested with `node --test`.
 */
export class ScrollEcho {
  private reportedPage: number | null = null;

  /** The view told its parent that `page` is now in view. */
  reported(page: number): void {
    this.reportedPage = page;
  }

  /** The page prop changed to `page`: should the view scroll to it? Consumes the echo. */
  shouldJump(page: number): boolean {
    const echo = this.reportedPage === page;
    this.reportedPage = null;
    return !echo;
  }
}
