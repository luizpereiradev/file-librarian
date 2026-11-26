// Render the menu bar poses (#112) into one contact sheet PNG, with AppKit:
// the same SVG renderer the menu bar applet uses, so what the sheet shows is
// what the menu bar shows. Ships with macOS; nothing to install.
//
//   osascript -l JavaScript scripts/menu-icons-sheet.js \
//       src/file_librarian/assets/menu docs/images/menu-poses.png
//
// Each column is a pose, in priority order. On a light and a dark menu bar:
// 18 pt at 1x (18 px), 18 pt at 2x (36 px), and the 1x pixels enlarged 6x so
// each one can be judged. The icons are tinted as macOS tints a template
// image: black ink on a light bar, white ink on a dark one. Below, the five
// side by side in a simulated menu bar (24 pt high, 1x and 2x, light and
// dark), spaced as status items are, to judge them together at real size.

ObjC.import("AppKit");

const POSES = ["busy", "due", "trouble", "attention", "asleep"]; // poses.PRIORITY
const COL = 130;
const LEFT = 120;
const ZOOM = 6;
const BARS = [
  { name: "light", bg: [0.93, 0.93, 0.93], ink: [0, 0, 0], text: [0.2, 0.2, 0.2] },
  { name: "dark", bg: [0.16, 0.16, 0.17], ink: [1, 1, 1], text: [0.85, 0.85, 0.85] },
];
const ROWS = [
  { label: "18 pt @1x", px: 18, zoom: 1, height: 40 },
  { label: "18 pt @2x", px: 36, zoom: 1, height: 58 },
  { label: "@1x × 6", px: 18, zoom: ZOOM, height: 18 * ZOOM + 22 },
];
const STRIP_GAP = 8; // points between status items, about what macOS leaves
const STRIP_ROWS = [
  { bar: 0, scale: 1 },
  { bar: 1, scale: 1 },
  { bar: 0, scale: 2 },
  { bar: 1, scale: 2 },
];
const STRIP_BAR = 24; // the menu bar's height in points

function color(rgb) {
  return $.NSColor.colorWithSRGBRedGreenBlueAlpha(rgb[0], rgb[1], rgb[2], 1);
}

// A template image drawn at `px` pixels, tinted with `ink`.
function tinted(path, px, ink) {
  const svg = $.NSImage.alloc.initWithContentsOfFile(path);
  if (!svg || svg.isNil()) throw new Error("cannot load " + path);
  const rep = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
    null, px, px, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0);
  rep.setSize($.NSMakeSize(px, px));
  const ctx = $.NSGraphicsContext.graphicsContextWithBitmapImageRep(rep);
  $.NSGraphicsContext.saveGraphicsState;
  $.NSGraphicsContext.setCurrentContext(ctx);
  const rect = $.NSMakeRect(0, 0, px, px);
  svg.drawInRectFromRectOperationFraction(rect, $.NSZeroRect, $.NSCompositingOperationSourceOver, 1);
  color(ink).set;
  $.NSRectFillUsingOperation(rect, $.NSCompositingOperationSourceIn);
  ctx.flushGraphics;
  $.NSGraphicsContext.restoreGraphicsState;
  return rep;
}

function text(str, x, y, rgb, size) {
  const attrs = $.NSMutableDictionary.dictionary;
  attrs.setObjectForKey($.NSFont.systemFontOfSize(size), $.NSFontAttributeName);
  attrs.setObjectForKey(color(rgb), $.NSForegroundColorAttributeName);
  $(str).drawAtPointWithAttributes($.NSMakePoint(x, y), attrs);
}

function run(argv) {
  const dir = argv[0];
  const out = argv[1];
  const bandHeight = ROWS.reduce((sum, r) => sum + r.height, 0) + 16;
  const width = LEFT + COL * POSES.length;
  const stripHeight = STRIP_ROWS.reduce((sum, r) => sum + STRIP_BAR * r.scale + 10, 0) + 26;
  const height = 30 + bandHeight * BARS.length + stripHeight;
  const sheet = $.NSBitmapImageRep.alloc.initWithBitmapDataPlanesPixelsWidePixelsHighBitsPerSampleSamplesPerPixelHasAlphaIsPlanarColorSpaceNameBytesPerRowBitsPerPixel(
    null, width, height, 8, 4, true, false, $.NSDeviceRGBColorSpace, 0, 0);
  sheet.setSize($.NSMakeSize(width, height));
  const ctx = $.NSGraphicsContext.graphicsContextWithBitmapImageRep(sheet);
  $.NSGraphicsContext.saveGraphicsState;
  $.NSGraphicsContext.setCurrentContext(ctx);
  ctx.setImageInterpolation($.NSImageInterpolationNone);

  color([1, 1, 1]).set;
  $.NSRectFill($.NSMakeRect(0, 0, width, height));
  POSES.forEach((pose, i) => text(pose, LEFT + i * COL, height - 22, [0.2, 0.2, 0.2], 13));

  // AppKit's origin is bottom-left: lay the bands out from the top down.
  let top = height - 30;
  for (const bar of BARS) {
    color(bar.bg).set;
    $.NSRectFill($.NSMakeRect(0, top - bandHeight, width, bandHeight));
    let y = top - 8;
    for (const row of ROWS) {
      y -= row.height;
      text(bar.name + " · " + row.label, 8, y + row.height / 2 - 7, bar.text, 11);
      const shown = row.px * row.zoom;
      POSES.forEach((pose, i) => {
        const rep = tinted(dir + "/" + pose + ".svg", row.px, bar.ink);
        const at = $.NSMakeRect(LEFT + i * COL, y + Math.floor((row.height - shown) / 2), shown, shown);
        rep.drawInRectFromRectOperationFractionRespectFlippedHints(
          at, $.NSZeroRect, $.NSCompositingOperationSourceOver, 1, false, $());
      });
    }
    top -= bandHeight;
  }

  // The strip: the five poses as they sit in a menu bar, at real size.
  text("in a menu bar, side by side", 8, top - 20, [0.2, 0.2, 0.2], 11);
  let y = top - 26;
  for (const row of STRIP_ROWS) {
    const bar = BARS[row.bar];
    const h = STRIP_BAR * row.scale;
    y -= h + 10;
    text(bar.name + " · @" + row.scale + "x", 8, y + h / 2 - 7, [0.2, 0.2, 0.2], 11);
    color(bar.bg).set;
    $.NSRectFill($.NSMakeRect(LEFT - 12, y, width - LEFT + 12, h));
    const px = 18 * row.scale;
    POSES.forEach((pose, i) => {
      const rep = tinted(dir + "/" + pose + ".svg", px, bar.ink);
      const x = LEFT + i * (18 + STRIP_GAP) * row.scale;
      const at = $.NSMakeRect(x, y + 3 * row.scale, px, px);
      rep.drawInRectFromRectOperationFractionRespectFlippedHints(
        at, $.NSZeroRect, $.NSCompositingOperationSourceOver, 1, false, $());
    });
  }
  ctx.flushGraphics;
  $.NSGraphicsContext.restoreGraphicsState;
  const png = sheet.representationUsingTypeProperties($.NSBitmapImageFileTypePNG, $.NSDictionary.dictionary);
  if (!png.writeToFileAtomically(out, true)) throw new Error("cannot write " + out);
  return out;
}
