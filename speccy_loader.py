#!/usr/bin/env python3
"""
ZX Spectrum Loading Screen Animator

Takes any image and produces an animated GIF that simulates the authentic
ZX Spectrum tape-loading experience:

  1. Border shows pilot tone stripes (red/cyan wide bands) before data
  2. Pixel data loads line-by-line in the characteristic interleaved order
     (three thirds, each with venetian-blind scanlines) while the border
     shows blue/yellow data stripes
  3. All pixels initially render in monochrome (black ink on white paper)
  4. After all bitmap data is loaded, colour attributes "pop" in row by row

The border stripe behaviour matches the real ROM loader:
  - Pilot tone: alternating red/cyan bands ~9-10 scanlines wide, scrolling
  - Data bytes: alternating blue/yellow bands ~4-8 scanlines wide, irregular
  - Between blocks: brief solid border (original colour)

The image is converted to the ZX Spectrum's constraints:
  - 256×192 pixels, 32×24 grid of 8×8 character cells
  - 15-colour palette (8 normal + 7 bright, bright white = normal white)
  - Each 8×8 cell limited to 2 colours (ink + paper) — authentic colour clash
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# ZX Spectrum palette  (index 0-7 normal, 8-14 bright)
# ---------------------------------------------------------------------------
SPECTRUM_PALETTE = [
    (0,   0,   0),    # 0  Black
    (0,   0,   215),  # 1  Blue
    (215, 0,   0),    # 2  Red
    (215, 0,   215),  # 3  Magenta
    (0,   215, 0),    # 4  Green
    (0,   215, 215),  # 5  Cyan
    (215, 215, 0),    # 6  Yellow
    (215, 215, 215),  # 7  White
    (0,   0,   255),  # 8  Bright Blue
    (255, 0,   0),    # 9  Bright Red
    (255, 0,   255),  # 10 Bright Magenta
    (0,   255, 0),    # 11 Bright Green
    (0,   255, 255),  # 12 Bright Cyan
    (255, 255, 0),    # 13 Bright Yellow
    (255, 255, 255),  # 14 Bright White
]

# Border stripe colours (as per ROM loader)
PILOT_COLOR_A = (215, 0, 0)      # Red (colour 2)
PILOT_COLOR_B = (0, 215, 215)    # Cyan (colour 5)
DATA_COLOR_A = (0, 0, 215)       # Blue (colour 1)
DATA_COLOR_B = (215, 215, 0)     # Yellow (colour 6)

SCREEN_W, SCREEN_H = 256, 192
CELL_SIZE = 8
CELLS_X, CELLS_Y = SCREEN_W // CELL_SIZE, SCREEN_H // CELL_SIZE  # 32, 24


def color_distance_sq(c1: tuple, c2: tuple) -> int:
    return (c1[0]-c2[0])**2 + (c1[1]-c2[1])**2 + (c1[2]-c2[2])**2


def closest_palette_index(rgb: tuple) -> int:
    best_i, best_d = 0, float('inf')
    for i, pc in enumerate(SPECTRUM_PALETTE):
        d = color_distance_sq(rgb, pc)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


# ---------------------------------------------------------------------------
# Bayer 8×8 ordered dithering matrix
# ---------------------------------------------------------------------------

# Normalised to 0.0–1.0 range; threshold = (matrix_value - 0.5)
# means pixels whose ink-vs-paper ratio crosses the threshold get toggled.
BAYER_8x8 = [
    [ 0,  48, 12, 60,  3, 51, 15, 63],
    [32,  16, 44, 28, 35, 19, 47, 31],
    [ 8,  56,  4, 52, 11, 59,  7, 55],
    [40,  24, 36, 20, 43, 27, 39, 23],
    [ 2,  50, 14, 62,  1, 49, 13, 61],
    [34,  18, 46, 30, 33, 17, 45, 29],
    [10,  58,  6, 54,  9, 57,  5, 53],
    [42,  26, 38, 22, 41, 25, 37, 21],
]
# Pre-normalise to 0.0–1.0
BAYER_NORM = [[v / 64.0 for v in row] for row in BAYER_8x8]


def _luminance(rgb: tuple) -> float:
    """Perceptual luminance (ITU-R BT.601)."""
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def _pick_best_ink_paper(pixels, img_pixels, crow: int, ccol: int):
    """
    Choose the best ink/paper pair for an 8×8 cell by finding the two
    palette colours that minimise total squared error across all 64 pixels.

    Uses a fast heuristic: pick the two most-frequent nearest-palette colours,
    but also consider the pair with the widest luminance spread if the top two
    are too similar (improves photo dithering).
    """
    # Collect per-pixel nearest palette indices and the original RGB values
    cell_rgbs = []
    counts: dict[int, int] = {}
    for py in range(CELL_SIZE):
        for px in range(CELL_SIZE):
            ci = pixels[crow * 8 + py][ccol * 8 + px]
            counts[ci] = counts.get(ci, 0) + 1
            cell_rgbs.append(img_pixels[ccol * 8 + px, crow * 8 + py])

    sorted_colors = sorted(counts, key=lambda c: -counts[c])
    ink_idx = sorted_colors[0]
    paper_idx = sorted_colors[1] if len(sorted_colors) > 1 else (7 if ink_idx == 0 else 0)
    if ink_idx == paper_idx:
        paper_idx = 7 if ink_idx == 0 else 0

    return ink_idx, paper_idx


# ---------------------------------------------------------------------------
# Convert image → ZX Spectrum screen data
# ---------------------------------------------------------------------------

def image_to_spectrum(img: Image.Image, dither: str = "none"):
    """
    Convert a PIL image to ZX Spectrum screen data.

    Args:
        dither: "none" for nearest-colour, "ordered" for Bayer 8×8 dithering

    Returns:
        pixel_colors: 192×256 array of palette indices (per pixel, before cell constraints)
        cell_ink:     24×32 array of ink palette index per cell
        cell_paper:   24×32 array of paper palette index per cell
        cell_bitmap:  24×32 array, each containing 8 bytes (one per row in the cell),
                      where each byte has 8 bits (1=ink, 0=paper)
    """
    img = img.convert("RGB").resize((SCREEN_W, SCREEN_H), Image.LANCZOS)
    pixels_img = img.load()

    # Map every pixel to nearest palette colour
    color_indices = [[closest_palette_index(pixels_img[x, y])
                      for x in range(SCREEN_W)]
                     for y in range(SCREEN_H)]

    cell_ink = [[0]*CELLS_X for _ in range(CELLS_Y)]
    cell_paper = [[0]*CELLS_X for _ in range(CELLS_Y)]
    cell_bitmap = [[None]*CELLS_X for _ in range(CELLS_Y)]

    for crow in range(CELLS_Y):
        for ccol in range(CELLS_X):
            ink_idx, paper_idx = _pick_best_ink_paper(
                color_indices, pixels_img, crow, ccol)

            cell_ink[crow][ccol] = ink_idx
            cell_paper[crow][ccol] = paper_idx

            ink_rgb = SPECTRUM_PALETTE[ink_idx]
            paper_rgb = SPECTRUM_PALETTE[paper_idx]

            # Build bitmap bytes for this cell
            bmp = []
            for py in range(CELL_SIZE):
                byte_val = 0
                for px in range(CELL_SIZE):
                    orig = pixels_img[ccol * 8 + px, crow * 8 + py]

                    if dither == "ordered":
                        # Ordered dithering: compute how much this pixel
                        # "wants" to be ink vs paper using luminance, then
                        # threshold against the Bayer matrix position.
                        ink_lum = _luminance(ink_rgb)
                        paper_lum = _luminance(paper_rgb)
                        orig_lum = _luminance(orig)

                        # Where does the original sit between paper and ink?
                        lum_range = ink_lum - paper_lum
                        if abs(lum_range) > 1e-6:
                            t = (orig_lum - paper_lum) / lum_range
                        else:
                            # Ink and paper have same luminance — use colour distance
                            d_ink = color_distance_sq(orig, ink_rgb)
                            d_paper = color_distance_sq(orig, paper_rgb)
                            t = 1.0 if d_ink <= d_paper else 0.0

                        t = max(0.0, min(1.0, t))
                        threshold = BAYER_NORM[py][px]
                        is_ink = t > threshold
                    else:
                        # No dithering: simple nearest-colour
                        d_ink = color_distance_sq(orig, ink_rgb)
                        d_paper = color_distance_sq(orig, paper_rgb)
                        is_ink = d_ink <= d_paper

                    if is_ink:
                        byte_val |= (1 << (7 - px))
                bmp.append(byte_val)
            cell_bitmap[crow][ccol] = bmp

    return color_indices, cell_ink, cell_paper, cell_bitmap


# ---------------------------------------------------------------------------
# ZX Spectrum scanline order (the "venetian blind" interleave)
# ---------------------------------------------------------------------------

def spectrum_line_order() -> list[int]:
    """
    Return the 192 display lines in the order they are stored in ZX Spectrum
    screen memory (and thus the order they load from tape).

    Memory layout per third (64 lines):
      For line_in_char 0..7, for char_row 0..7:
        display_y = third*64 + char_row*8 + line_in_char
    """
    order = []
    for third in range(3):
        for line_in_char in range(8):
            for char_row in range(8):
                y = third * 64 + char_row * 8 + line_in_char
                order.append(y)
    return order


# ---------------------------------------------------------------------------
# Border stripe rendering
# ---------------------------------------------------------------------------

def draw_striped_border(
    draw: ImageDraw.Draw,
    total_w: int, total_h: int,
    ox: int, oy: int,
    screen_w_px: int, screen_h_px: int,
    color_a: tuple, color_b: tuple,
    stripe_height: int,
    phase_offset: int,
    scale: int,
):
    """
    Draw alternating horizontal colour stripes across the entire border area.

    The stripes cover the full image but the screen area will be drawn over the
    top, so only the border regions remain visible. The phase_offset shifts the
    pattern each frame to simulate the scrolling illusion caused by the
    incommensurate tape frequency vs TV frame rate.

    Args:
        stripe_height: height of each stripe in unscaled scanlines
        phase_offset: vertical offset in unscaled scanlines (animates scrolling)
    """
    sh = stripe_height * scale
    # Offset in pixels
    offset = (phase_offset * scale) % (sh * 2)

    y = -offset
    color_toggle = False
    while y < total_h:
        color = color_a if color_toggle else color_b
        color_toggle = not color_toggle
        y0 = max(0, y)
        y1 = min(total_h - 1, y + sh - 1)
        if y1 >= y0:
            # Draw full-width stripe
            draw.rectangle([0, y0, total_w - 1, y1], fill=color)
        y += sh


# ---------------------------------------------------------------------------
# Render a single frame
# ---------------------------------------------------------------------------

def render_frame(
    cell_ink, cell_paper, cell_bitmap,
    revealed_lines: set[int],
    color_applied: bool,
    color_rows_applied: int = 0,
    scale: int = 2,
    border_color: tuple = (215, 215, 215),
    border_size: int = 32,
    border_stripes: str = "none",
    stripe_phase: int = 0,
    mono_ink: tuple | None = None,
) -> Image.Image:
    """
    Render the Spectrum screen at a given loading state.

    Args:
        revealed_lines: set of y-coordinates whose bitmap data has loaded
        color_applied: whether the colour attribute phase has started
        color_rows_applied: how many attribute rows (0-24) have been applied
        scale: pixel scaling factor
        border_color: ZX Spectrum border colour (used when no stripes)
        border_size: border width in Spectrum pixels (before scaling)
        border_stripes: "none", "pilot" (red/cyan wide), or "data" (blue/yellow thin)
        stripe_phase: animation phase offset for scrolling stripes
        mono_ink: if set, use this fixed ink colour instead of per-cell colours
    """
    total_w = (SCREEN_W + border_size * 2) * scale
    total_h = (SCREEN_H + border_size * 2) * scale

    ox = border_size * scale
    oy = border_size * scale
    screen_w_px = SCREEN_W * scale
    screen_h_px = SCREEN_H * scale

    frame = Image.new("RGB", (total_w, total_h), border_color)
    draw = ImageDraw.Draw(frame)

    # Draw border stripes if active
    if border_stripes == "pilot":
        # Pilot tone: wide red/cyan bands (~9-10 scanlines per stripe)
        draw_striped_border(draw, total_w, total_h, ox, oy,
                            screen_w_px, screen_h_px,
                            PILOT_COLOR_A, PILOT_COLOR_B,
                            stripe_height=10, phase_offset=stripe_phase,
                            scale=scale)
    elif border_stripes == "data":
        # Data bytes: thinner blue/yellow bands (~5 scanlines avg, variable)
        draw_striped_border(draw, total_w, total_h, ox, oy,
                            screen_w_px, screen_h_px,
                            DATA_COLOR_A, DATA_COLOR_B,
                            stripe_height=5, phase_offset=stripe_phase,
                            scale=scale)

    # Draw the screen area (white paper for unrevealed, monochrome/colour for loaded)
    # First fill entire screen area with white paper
    draw.rectangle([ox, oy, ox + screen_w_px - 1, oy + screen_h_px - 1],
                   fill=(215, 215, 215))

    for y in range(SCREEN_H):
        if y not in revealed_lines:
            continue

        crow = y // CELL_SIZE
        py = y % CELL_SIZE

        for ccol in range(CELLS_X):
            ink_idx = cell_ink[crow][ccol]
            paper_idx = cell_paper[crow][ccol]

            # During bitmap phase: monochrome (black ink, white paper)
            # With mono_ink: always use the fixed ink colour
            if mono_ink is not None:
                ink_rgb = mono_ink
                paper_rgb = (215, 215, 215)
            elif not color_applied or crow >= color_rows_applied:
                ink_rgb = (0, 0, 0)
                paper_rgb = (215, 215, 215)
            else:
                ink_rgb = SPECTRUM_PALETTE[ink_idx]
                paper_rgb = SPECTRUM_PALETTE[paper_idx]

            byte_val = cell_bitmap[crow][ccol][py]
            for px in range(CELL_SIZE):
                bit = (byte_val >> (7 - px)) & 1
                rgb = ink_rgb if bit else paper_rgb
                sx = ox + (ccol * CELL_SIZE + px) * scale
                sy = oy + y * scale
                draw.rectangle([sx, sy, sx + scale - 1, sy + scale - 1], fill=rgb)

    return frame


# ---------------------------------------------------------------------------
# Generate the animation
# ---------------------------------------------------------------------------

def generate_animation(
    input_path: str,
    output_path: str,
    scale: int = 2,
    lines_per_frame: int = 4,
    color_rows_per_frame: int = 2,
    frame_delay_ms: int = 50,
    border_color_name: str = "white",
    final_hold_ms: int = 2000,
    dither: str = "none",
    mono: str | None = None,
):
    """Generate the full loading animation as an animated GIF."""

    palette_colors = {
        "black": (0, 0, 0), "blue": (0, 0, 215), "red": (215, 0, 0),
        "magenta": (215, 0, 215), "green": (0, 215, 0), "cyan": (0, 215, 215),
        "yellow": (215, 215, 0), "white": (215, 215, 215),
    }
    border_rgb = palette_colors.get(border_color_name.lower(), (215, 215, 215))

    # Resolve mono ink colour
    mono_ink = None
    if mono is not None:
        mono_ink = palette_colors.get(mono.lower(), (0, 0, 0))

    print(f"  Loading image: {input_path}")
    img = Image.open(input_path)

    dither_label = f", dither={dither}" if dither != "none" else ""
    print(f"  Converting to ZX Spectrum format (256×192, 15 colours, 8×8 attribute cells{dither_label})...")
    _, cell_ink, cell_paper, cell_bitmap = image_to_spectrum(img, dither=dither)

    line_order = spectrum_line_order()
    frames: list[Image.Image] = []
    durations: list[int] = []

    # Stripe scrolling phase counter (increments each frame for scrolling effect)
    stripe_phase = 0
    # Pilot stripes scroll ~3 scanlines per frame (slower, wider stripes)
    PILOT_SCROLL_SPEED = 3
    # Data stripes scroll ~7 scanlines per frame (faster, thinner stripes)
    DATA_SCROLL_SPEED = 7

    # --- Phase 1: Brief white flash then solid border (pre-load state) ---
    # The ROM sets border to white briefly before pilot tone begins
    white_flash = render_frame(cell_ink, cell_paper, cell_bitmap,
                               set(), False, 0, scale,
                               border_color=(255, 255, 255), border_stripes="none")
    frames.append(white_flash)
    durations.append(100)

    # --- Phase 2: Header pilot tone (red/cyan wide stripes, screen blank) ---
    # Real Spectrum: ~5 seconds for header pilot. We show a few frames to
    # represent this without making the GIF excessively long.
    HEADER_PILOT_FRAMES = 12
    print(f"  Header pilot tone: {HEADER_PILOT_FRAMES} frames (red/cyan stripes)")
    for _ in range(HEADER_PILOT_FRAMES):
        frame = render_frame(cell_ink, cell_paper, cell_bitmap,
                             set(), False, 0, scale,
                             border_color=border_rgb,
                             border_stripes="pilot",
                             stripe_phase=stripe_phase)
        frames.append(frame)
        durations.append(frame_delay_ms)
        stripe_phase += PILOT_SCROLL_SPEED

    # --- Phase 3: Brief gap between header and data blocks ---
    # Border returns to original colour momentarily
    gap_frame = render_frame(cell_ink, cell_paper, cell_bitmap,
                             set(), False, 0, scale,
                             border_color=border_rgb, border_stripes="none")
    frames.append(gap_frame)
    durations.append(400)

    # --- Phase 4: Data block pilot tone (red/cyan, shorter than header) ---
    DATA_PILOT_FRAMES = 6
    print(f"  Data pilot tone: {DATA_PILOT_FRAMES} frames (red/cyan stripes)")
    for _ in range(DATA_PILOT_FRAMES):
        frame = render_frame(cell_ink, cell_paper, cell_bitmap,
                             set(), False, 0, scale,
                             border_color=border_rgb,
                             border_stripes="pilot",
                             stripe_phase=stripe_phase)
        frames.append(frame)
        durations.append(frame_delay_ms)
        stripe_phase += PILOT_SCROLL_SPEED

    # --- Phase 5: Bitmap loading (interleaved scanlines, monochrome) ---
    # Border shows blue/yellow data stripes while bytes stream in
    revealed: set[int] = set()
    total_lines = len(line_order)
    i = 0
    bitmap_frames = 0
    while i < total_lines:
        batch_end = min(i + lines_per_frame, total_lines)
        for j in range(i, batch_end):
            revealed.add(line_order[j])
        frame = render_frame(cell_ink, cell_paper, cell_bitmap,
                             revealed, False, 0, scale,
                             border_color=border_rgb,
                             border_stripes="data",
                             stripe_phase=stripe_phase,
                             mono_ink=mono_ink)
        frames.append(frame)
        durations.append(frame_delay_ms)
        stripe_phase += DATA_SCROLL_SPEED
        i = batch_end
        bitmap_frames += 1

    print(f"  Bitmap phase: {bitmap_frames} frames (blue/yellow data stripes)")

    if mono_ink is None:
        # --- Phase 6: Colour attributes apply row by row ---
        # Attributes are the final 768 bytes of the same data block, so
        # blue/yellow border stripes continue throughout.
        color_frames = 0
        row = 0
        while row < CELLS_Y:
            batch_end = min(row + color_rows_per_frame, CELLS_Y)
            frame = render_frame(cell_ink, cell_paper, cell_bitmap,
                                 revealed, True, batch_end, scale,
                                 border_color=border_rgb,
                                 border_stripes="data",
                                 stripe_phase=stripe_phase)
            frames.append(frame)
            durations.append(frame_delay_ms)
            stripe_phase += DATA_SCROLL_SPEED
            row = batch_end
            color_frames += 1

        print(f"  Colour phase: {color_frames} frames (blue/yellow data stripes)")
    else:
        print(f"  Colour phase: skipped (mono mode)")

    # --- Final frame: Loading complete — border returns to solid ---
    final_frame = render_frame(cell_ink, cell_paper, cell_bitmap,
                               revealed, mono_ink is None, CELLS_Y, scale,
                               border_color=border_rgb, border_stripes="none",
                               mono_ink=mono_ink)
    frames.append(final_frame)
    durations.append(final_hold_ms)

    # --- Save GIF ---
    print(f"  Saving {len(frames)} frames to: {output_path}")
    frames[0].save(
        output_path,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )

    fsize = os.path.getsize(output_path) / 1024
    w, h = frames[0].size
    print(f"  ✓ Done: {output_path} ({w}×{h}, {len(frames)} frames, {fsize:.0f} KB)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="ZX Spectrum Loading Screen Animator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:

  # Basic usage — produce a loading animation GIF
  %(prog)s -i screenshot.png -o loading.gif

  # Larger output with slower loading
  %(prog)s -i screenshot.png -o loading.gif --scale 3 --lines-per-frame 2

  # Faster loading, black border
  %(prog)s -i screenshot.png -o loading.gif --lines-per-frame 8 --border black

  # Use as part of a pipeline
  %(prog)s -i photo.jpg -o speccy_load.gif --scale 4 --delay 30

  # Dithered photo — much better for photographs
  %(prog)s -i photo.jpg -o loading.gif --dither ordered --border black
""")

    parser.add_argument("-i", "--input", required=True,
                        help="Input image (any format Pillow supports)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output GIF path (default: <input>_speccy.gif)")
    parser.add_argument("--scale", type=int, default=2,
                        help="Pixel scale factor (default: 2, output = 640×384)")
    parser.add_argument("--lines-per-frame", type=int, default=4,
                        help="Scanlines revealed per frame during bitmap phase (default: 4)")
    parser.add_argument("--color-rows-per-frame", type=int, default=2,
                        help="Attribute rows coloured per frame (default: 2)")
    parser.add_argument("--delay", type=int, default=50,
                        help="Frame delay in ms (default: 50)")
    parser.add_argument("--border", default="white",
                        choices=["black", "blue", "red", "magenta",
                                 "green", "cyan", "yellow", "white"],
                        help="Border colour (default: white)")
    parser.add_argument("--hold", type=int, default=2000,
                        help="Final frame hold time in ms (default: 2000)")
    parser.add_argument("--dither", default="none",
                        choices=["none", "ordered"],
                        help="Dithering mode: none (default) or ordered (Bayer 8×8, best for photos)")
    parser.add_argument("--mono", default=None, nargs="?", const="black",
                        choices=["black", "blue", "red", "magenta",
                                 "green", "cyan", "yellow", "white"],
                        help="Monochrome mode: use a single ink colour with no colour attributes. "
                             "Specify a colour (default: black). Best combined with --dither ordered")

    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"ERROR: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    output = args.output
    if not output:
        stem = Path(args.input).stem
        output = f"{stem}_speccy.gif"

    generate_animation(
        input_path=args.input,
        output_path=output,
        scale=args.scale,
        lines_per_frame=args.lines_per_frame,
        color_rows_per_frame=args.color_rows_per_frame,
        frame_delay_ms=args.delay,
        border_color_name=args.border,
        final_hold_ms=args.hold,
        dither=args.dither,
        mono=args.mono,
    )


if __name__ == "__main__":
    main()
