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

/** A screen rectangle, in CSS pixels. */
export interface Area {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** What sits over the tree and should be kept clear when fitting it. */
export interface Obstacles {
  /** Screen pixels at the top taken by the tier header. */
  header: number;
  /** The control dock, or null when it takes no space. */
  dock: Area | null;
}

/** Breathing room between the tree and whatever is beside it. */
const GAP = 8;

/**
 * The part of the screen to fit content into: beside the dock or below it,
 * whichever shows the content larger, less what the detail panel covers.
 *
 * The tree is about as tall as it is wide, so on a landscape screen its fit is
 * set by height, and a narrow dock at the left costs it nothing; on a phone the
 * dock spans the top, and the tree goes below.
 */
export function fitArea(
  viewport: Viewport,
  content: { width: number; height: number },
  obstacles: Obstacles,
  rightInset = 0,
  bottomInset = 0,
): Area {
  const right = viewport.width - rightInset;
  const bottom = viewport.height - bottomInset;
  const top = obstacles.header + GAP;
  const dock = obstacles.dock;
  if (dock === null) return { left: 0, top, right, bottom };
  const beside = { left: dock.right + GAP, top, right, bottom };
  const below = { left: 0, top: Math.max(top, dock.bottom + GAP), right, bottom };
  const scale = (area: Area) =>
    Math.min((area.right - area.left) / content.width, (area.bottom - area.top) / content.height);
  return scale(beside) >= scale(below) ? beside : below;
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
    /** What covers the tree. Read each time: the dock grows when it shows
     *  the isolation chip or wraps its toggles. */
    private readonly obstacles: () => Obstacles = () => ({ header: 0, dock: null }),
  ) {}

  setViewport(viewport: Viewport): void {
    this.viewport = viewport;
    this.clamp();
  }

  /** The clear part of the screen the tree is fitted and centred in. */
  clearArea(): Area {
    return fitArea(this.viewport, this.size(), this.obstacles(), this.rightInset, this.bottomInset);
  }

  /** Fit the whole tree into the clear part of the screen. */
  fit(): void {
    const content = this.size();
    const area = this.clearArea();
    const width = area.right - area.left;
    const height = area.bottom - area.top;
    this.scale = Math.max(this.minScale, Math.min(width / content.width, height / content.height) * 0.98);
    this.x = area.left + (width - content.width * this.scale) / 2;
    this.y = area.top + (height - content.height * this.scale) / 2;
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

    // When the content is smaller than the clear area, centre it there instead:
    // clear of the dock and of the panel.
    const area = this.clearArea();
    const width = area.right - area.left;
    const height = area.bottom - area.top;
    if (scaledWidth < width) this.x = area.left + (width - scaledWidth) / 2;
    if (scaledHeight < height) this.y = area.top + (height - scaledHeight) / 2;
  }
}
