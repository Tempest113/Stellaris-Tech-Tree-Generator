/**
 * Pan and zoom.
 *
 * A plain scale-and-translate transform. Zoom is clamped so the whole canvas
 * can be seen at once at the low end, and a card is comfortably readable at the
 * high end; panning is clamped so the content cannot be lost off screen.
 */

export interface Viewport {
  width: number;
  height: number;
}

export class Camera {
  scale = 0.25;
  x = 0;
  y = 0;

  minScale = 0.04;
  maxScale = 2;
  /** Screen pixels at the right covered by the detail panel, kept clear by
   *  `fit` and by centring. */
  rightInset = 0;
  /** The same at the bottom, where a phone shows the panel as a sheet. */
  bottomInset = 0;

  constructor(
    /** The size of what is shown, which changes with the view. */
    private readonly size: () => { width: number; height: number },
    private viewport: Viewport,
    /** Screen pixels at the top covered by the header and toolbar, kept clear by
     *  `fit`. Read each time: the toolbar grows when it wraps or shows the
     *  isolation chip. */
    private readonly topInset: () => number = () => 0,
  ) {}

  setViewport(viewport: Viewport): void {
    this.viewport = viewport;
    this.clamp();
  }

  /** Fit the whole tree on screen, below the header. */
  fit(): void {
    const content = this.size();
    const top = this.topInset();
    const height = this.viewport.height - top - this.bottomInset;
    const width = this.viewport.width - this.rightInset;
    const sx = width / content.width;
    const sy = height / content.height;
    this.scale = Math.max(this.minScale, Math.min(sx, sy) * 0.98);
    this.x = (width - content.width * this.scale) / 2;
    this.y = top + (height - content.height * this.scale) / 2;
  }

  panBy(dx: number, dy: number): void {
    this.x += dx;
    this.y += dy;
    this.clamp();
  }

  /** Zoom about a screen point, so the content under the cursor stays put. */
  zoomAt(screenX: number, screenY: number, factor: number): void {
    const next = Math.max(this.minScale, Math.min(this.maxScale, this.scale * factor));
    if (next === this.scale) return;
    const worldX = (screenX - this.x) / this.scale;
    const worldY = (screenY - this.y) / this.scale;
    this.scale = next;
    this.x = screenX - worldX * this.scale;
    this.y = screenY - worldY * this.scale;
    this.clamp();
  }

  centreOn(worldX: number, worldY: number, scale?: number): void {
    if (scale !== undefined) {
      this.scale = Math.max(this.minScale, Math.min(this.maxScale, scale));
    }
    this.x = this.viewport.width / 2 - worldX * this.scale;
    this.y = this.viewport.height / 2 - worldY * this.scale;
    this.clamp();
  }

  toWorld(screenX: number, screenY: number): { x: number; y: number } {
    return { x: (screenX - this.x) / this.scale, y: (screenY - this.y) / this.scale };
  }

  /** World-space rectangle currently visible, used for culling. */
  visibleBounds(padding = 0): { x0: number; y0: number; x1: number; y1: number } {
    const topLeft = this.toWorld(0, 0);
    const bottomRight = this.toWorld(this.viewport.width, this.viewport.height);
    return {
      x0: topLeft.x - padding,
      y0: topLeft.y - padding,
      x1: bottomRight.x + padding,
      y1: bottomRight.y + padding,
    };
  }

  /** Re-apply the pan limits, after what is shown has changed size. */
  clamp(): void {
    // Always leave a screen-third of content in view, in both directions.
    const slackX = this.viewport.width / 3;
    const slackY = this.viewport.height / 3;
    const content = this.size();
    const scaledWidth = content.width * this.scale;
    const scaledHeight = content.height * this.scale;

    this.x = Math.min(slackX, Math.max(this.viewport.width - scaledWidth - slackX, this.x));
    this.y = Math.min(slackY, Math.max(this.viewport.height - scaledHeight - slackY, this.y));

    // When the content is smaller than the viewport, centre instead of clamping:
    // in the part of it the panel leaves uncovered.
    const width = this.viewport.width - this.rightInset;
    if (scaledWidth < width) this.x = (width - scaledWidth) / 2;
    const height = this.viewport.height - this.bottomInset;
    if (scaledHeight < height) this.y = (height - scaledHeight) / 2;
  }
}
