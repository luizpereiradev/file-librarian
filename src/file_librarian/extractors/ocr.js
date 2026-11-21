// Text recognition with macOS's Vision framework, for extractors/ocr.py (RF-26).
//
// Run with /usr/bin/osascript -l JavaScript, which ships with every Mac: no
// compiler, no download. Everything variable arrives as an argument, never as
// script source:
//
//   ocr.js recognize FILE MAX_PAGES MAX_PX LANGUAGE...
//       Prints what it found in FILE (an image, or the first MAX_PAGES pages
//       of a PDF rendered with PDFKit) as one line of JSON (UTF-8) on stdout:
//         {"v": 2, "pages": [{"width": W, "height": H,
//                             "lines": [{"text": T, "box": [X, Y, BW, BH]}]}]}
//       WIDTH and HEIGHT are the picture's own size in pixels (an image's as
//       stored, a page's as rendered); each box is where Vision found that
//       line, as fractions of the picture (0 to 1, origin bottom left). Each
//       picture is decoded or rendered at most MAX_PX pixels on its longer
//       side, which bounds memory, and is drawn on white so transparent text
//       shows.
//   ocr.js probe
//       Prints the recognition languages Vision supports, one per line.
//
// Exit codes: 0 done (the text may be empty), 2 the file cannot be read as
// an image or PDF, 3 recognition failed, 4 bad arguments.

ObjC.import('Foundation');
ObjC.import('stdlib');
ObjC.import('CoreGraphics');
ObjC.import('ImageIO');
ObjC.import('Vision');
ObjC.import('PDFKit');
ObjC.import('AppKit');

function write(handle, text) {
  handle.writeData($(text).dataUsingEncoding($.NSUTF8StringEncoding));
}

function fail(code, message) {
  write($.NSFileHandle.fileHandleWithStandardError, message + '\n');
  $.exit(code);
}

function request(languages) {
  var req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = $.VNRequestTextRecognitionLevelAccurate;
  req.usesLanguageCorrection = true;
  if (languages.length > 0) req.recognitionLanguages = $(languages);
  return req;
}

function supported() {
  var list = request([]).supportedRecognitionLanguagesAndReturnError(null);
  var out = [];
  for (var i = 0; list && i < list.count; i++) out.push(list.objectAtIndex(i).js);
  return out;
}

// A copy of an image at most maxPx on its longer side, on an opaque white background.
function flatten(image, maxPx) {
  var w = $.CGImageGetWidth(image), h = $.CGImageGetHeight(image);
  if (!w || !h) fail(2, 'empty image');
  var scale = Math.min(1, maxPx / Math.max(w, h));
  var tw = Math.max(1, Math.round(w * scale)), th = Math.max(1, Math.round(h * scale));
  var ctx = $.CGBitmapContextCreate(null, tw, th, 8, 0, $.CGColorSpaceCreateDeviceRGB(), 1);
  if (!ctx) fail(3, 'cannot allocate the image');
  $.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1);
  $.CGContextFillRect(ctx, $.CGRectMake(0, 0, tw, th));
  $.CGContextDrawImage(ctx, $.CGRectMake(0, 0, tw, th), image);
  return $.CGBitmapContextCreateImage(ctx);
}

function round4(x) {
  return Math.round(x * 10000) / 10000;
}

function recognize(image, languages) {
  var req = request(languages);
  var handler = $.VNImageRequestHandler.alloc.initWithCGImageOptions(image, $.NSDictionary.dictionary);
  if (!handler.performRequestsError($.NSArray.arrayWithObject(req), null)) {
    fail(3, 'text recognition failed');
  }
  var lines = [];
  var results = req.results;
  for (var i = 0; results && i < results.count; i++) {
    var observation = results.objectAtIndex(i);
    var candidates = observation.topCandidates(1);
    if (candidates.count < 1) continue;
    var b = observation.boundingBox;
    lines.push({
      text: candidates.objectAtIndex(0).string.js,
      box: [round4(b.origin.x), round4(b.origin.y), round4(b.size.width), round4(b.size.height)],
    });
  }
  return lines;
}

function pdfPages(url, maxPages, maxPx) {
  var doc = $.PDFDocument.alloc.initWithURL(url);
  if (doc.isNil()) fail(2, 'not a readable PDF');
  if (doc.isLocked) fail(2, 'password-protected PDF');
  var images = [];
  var count = Math.min(doc.pageCount, maxPages);
  for (var p = 0; p < count; p++) {
    var page = doc.pageAtIndex(p);
    var box = page.boundsForBox($.kPDFDisplayBoxMediaBox);
    var longer = Math.max(box.size.width, box.size.height);
    if (!(longer > 0)) continue;
    var scale = maxPx / longer;
    var size = $.NSMakeSize(Math.round(box.size.width * scale), Math.round(box.size.height * scale));
    var picture = page.thumbnailOfSizeForBox(size, $.kPDFDisplayBoxMediaBox);
    var image = picture.CGImageForProposedRectContextHints(null, $(), $.NSDictionary.dictionary);
    if (image) images.push({ image: flatten(image, maxPx), width: size.width, height: size.height });
  }
  return images;
}

function imageFile(url, maxPx) {
  var source = $.CGImageSourceCreateWithURL(url, null);
  if (!source || $.CGImageSourceGetCount(source) < 1) fail(2, 'not a readable image');
  // Decoded straight at the reduced size, turned upright from its EXIF orientation.
  var options = $.NSDictionary.dictionaryWithObjectsForKeys(
    $([true, true, maxPx]),
    $([
      'kCGImageSourceCreateThumbnailFromImageAlways',
      'kCGImageSourceCreateThumbnailWithTransform',
      'kCGImageSourceThumbnailMaxPixelSize',
    ])
  );
  var image = $.CGImageSourceCreateThumbnailAtIndex(source, 0, options);
  if (!image) fail(2, 'not a readable image');
  var size = storedSize(source, image, maxPx);
  return [{ image: flatten(image, maxPx), width: size[0], height: size[1] }];
}

// The image's size as stored, upright like the thumbnail. The thumbnail is
// never enlarged, so only a reduced one needs the stored longer side.
function storedSize(source, thumbnail, maxPx) {
  var tw = $.CGImageGetWidth(thumbnail), th = $.CGImageGetHeight(thumbnail);
  if (Math.max(tw, th) < maxPx) return [tw, th];
  var props = ObjC.castRefToObject($.CGImageSourceCopyPropertiesAtIndex(source, 0, $()));
  var w = props.isNil() ? null : props.objectForKey('PixelWidth');
  var h = props.isNil() ? null : props.objectForKey('PixelHeight');
  if (!w || !h || w.isNil() || h.isNil()) return [tw, th];
  var scale = Math.max(w.intValue, h.intValue) / Math.max(tw, th);
  return [Math.round(tw * scale), Math.round(th * scale)];
}

function run(argv) {
  if (argv[0] === 'probe') {
    write($.NSFileHandle.fileHandleWithStandardOutput, supported().join('\n'));
    return;
  }
  if (argv[0] !== 'recognize' || argv.length < 4) fail(4, 'usage: ocr.js recognize FILE MAX_PAGES MAX_PX LANGUAGE...');
  var path = argv[1];
  var maxPages = parseInt(argv[2], 10);
  var maxPx = parseInt(argv[3], 10);
  if (!(maxPages > 0) || !(maxPx > 0)) fail(4, 'MAX_PAGES and MAX_PX must be positive');
  var available = supported();
  var languages = argv.slice(4).filter(function (l) { return available.indexOf(l) >= 0; });
  var url = $.NSURL.fileURLWithPath(path);
  var images = /\.pdf$/i.test(path) ? pdfPages(url, maxPages, maxPx) : imageFile(url, maxPx);
  var pages = [];
  for (var i = 0; i < images.length; i++) {
    pages.push({
      width: Math.round(images[i].width),
      height: Math.round(images[i].height),
      lines: recognize(images[i].image, languages),
    });
  }
  write($.NSFileHandle.fileHandleWithStandardOutput, JSON.stringify({ v: 2, pages: pages }));
}
