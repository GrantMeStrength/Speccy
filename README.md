# ZX Spectrum Loading Screen Animator

Converts any image into an animated GIF that simulates the authentic ZX Spectrum tape-loading experience — including the iconic border stripes.

![Speccy loading animation](loading.gif)

## The Effect

The animation faithfully recreates what you'd see on a real Spectrum loading from tape:

1. **White flash** — brief border flash as the ROM loader initialises
2. **Pilot tone** — red/cyan striped border (wide bands, scrolling)
3. **Gap** — brief solid border between header and data blocks
4. **Data pilot** — red/cyan stripes again (shorter, for the data block)
5. **Bitmap loads** — blue/yellow striped border while scanlines appear in the characteristic interleaved "venetian blind" order (3 thirds, each with interlaced scanlines) — displayed in monochrome
6. **Colour attributes pop in** — blue/yellow stripes continue while colour "pops" in row by row with authentic colour clash
7. **Loading complete** — border returns to solid colour

The border stripe colours match the real ROM loader behaviour: **red/cyan** during the pilot tone, switching to **blue/yellow** for data bytes (via `XOR $03` on the border register).

## Setup

```bash
pip3 install -r requirements.txt
```

## Usage

```bash
# Basic — convert any image to a Spectrum loading animation
python3 speccy_loader.py -i photo.jpg -o loading.gif

# With ordered dithering — much better for photographs
python3 speccy_loader.py -i photo.jpg -o loading.gif --dither ordered --border black

# Monochrome dithered — black & white shading only, no colour attributes
python3 speccy_loader.py -i photo.jpg -o loading.gif --dither ordered --mono --border black

# Monochrome with a specific ink colour
python3 speccy_loader.py -i photo.jpg -o loading.gif --dither ordered --mono blue --border black

# Larger output, slower loading for dramatic effect
python3 speccy_loader.py -i screenshot.png -o loading.gif --scale 3 --lines-per-frame 2

# Faster loading
python3 speccy_loader.py -i screenshot.png -o loading.gif --lines-per-frame 8 --border black
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `-i`, `--input` | (required) | Input image (any format Pillow supports) |
| `-o`, `--output` | `<input>_speccy.gif` | Output GIF path |
| `--scale` | `2` | Pixel scale (2 = 640×512 output) |
| `--lines-per-frame` | `4` | Scanlines per frame in bitmap phase |
| `--color-rows-per-frame` | `2` | Attribute rows coloured per frame |
| `--delay` | `50` | Frame delay in milliseconds |
| `--border` | `white` | Border colour (black/blue/red/magenta/green/cyan/yellow/white) |
| `--hold` | `2000` | Final frame hold time in ms |
| `--dither` | `none` | Dithering mode: `none` or `ordered` (Bayer 8×8 matrix) |
| `--mono` | *(off)* | Monochrome mode: use a single ink colour (default: black) with no colour attributes. Accepts: black/blue/red/magenta/green/cyan/yellow/white |

## Dithering

The `--dither ordered` option uses a Bayer 8×8 matrix to create halftone-style dot patterns within each 8×8 character cell. This simulates intermediate tones using just the two colours available per cell — exactly as many real Spectrum art tools did. It makes a huge difference for photographs and images with smooth gradients.

| No dithering | Ordered dithering |
|:---:|:---:|
| Flat blocks per cell | Smooth tonal gradients |

## How It Works

The ZX Spectrum screen is 256×192 pixels, divided into a 32×24 grid of 8×8 character cells. Each cell can only have 2 colours (ink + paper) — this limitation causes the famous "colour clash" effect.

Screen memory is laid out in a non-linear interleaved order across three thirds (lines 0–63, 64–127, 128–191). Within each third, scanlines load in the pattern: line 0, 8, 16, 24, 32, 40, 48, 56, then 1, 9, 17… creating the characteristic "venetian blind" appearance during loading.

The colour attributes (768 bytes) are stored after the bitmap data (6144 bytes) in the same data block, so during loading the image appears in black and white first, then colour "pops" in at the end.

### Border stripes

During tape loading, the ROM loader toggles the border colour on every detected tape signal edge:

- **Pilot tone**: Alternates between red (colour 2) and cyan (colour 5) — wide bands ~9–10 scanlines each, appearing to scroll due to the incommensurate pilot frequency vs TV frame rate
- **Data bytes**: After the sync pulse, the ROM switches to blue (colour 1) and yellow (colour 6) — thinner, faster, variable-width stripes matching the mix of 0-bit and 1-bit pulse widths

This is controlled by the `LD_EDGE_1` subroutine in the 48K ROM at address `$05E3`, which complements the C register and outputs the low 3 bits to port `$FE` on every edge detection.
