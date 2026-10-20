// Short tool and parameter descriptions: what Claude sees in the tool list (0.9.1).
// Kept to what is needed to pick a tool, its key options and the one-line pointer to the
// usually better tool; details live in README.md. Applied at
// tools/list time to web-reader's own tools and the devtools engine tools alike, so the
// long texts in server.mjs / search.mjs stay as the detailed reference.

const DT = ' [devtools: page_id from open_page, uids from take_snapshot]';

export const DESC = {
  // reading
  read_smart: 'Read a URL in the real browser (the one reading tool). Usually fast:true first (falls back to rendering by itself); leave fast off for values scripts fill in (prices, stock, live status). Options: section, selector, mode, include_links, structured_data, embedded_json, images, auto_scroll, alternates/lang_fallback. needs_human → tell the user, then wait_for_human. Searching: web_search.',
  web_search: 'Web search (Google, then Bing/DuckDuckGo if blocked): title, URL, snippet; fetch_top also reads the top N results. Filter with site / exclude_sites / prefer_detail.',
  get_links: 'Links on a rendered page (text + absolute URL).',
  check_domains: 'Which sites open from this PC: status, redirect, title, bot check, error kind. use_browser for JS bot checks.',
  collect_post: 'Forum post as data: title, body and all comments (dcinside precise, other sites best effort).',
  hotel_search: 'Google Hotels for fixed dates: name, nightly price, rating, reviews, stars, amenities.',
  transit_route: 'Google Maps route options: duration, times, line/road, fare. mode transit (default) / driving / walking / bicycling.',
  // images, maps
  page_images: 'Images on a page (og, JSON-LD, img/srcset/lazy, alt, size). Download with fetch_images.',
  find_place_photos: 'Real photos of a named place via image search (URL, source page, size, name_match). Check with contact_sheet.',
  fetch_images: 'Download many images through the browser (same cookies/UA, pacing, backoff, cache, optional resize/thumbnails) into the data folder.',
  contact_sheet: 'One numbered thumbnail grid from downloaded files or image URLs, to check photos at a glance.',
  static_map: 'Map PNG with numbered pins, labels and an optional route line.',
  save_places: 'Google Maps: save / remove / check many place links in one named list (up to 4 tabs, verified per link). Needs the browser logged in to Google. Re-check after ~1 min.',
  // open pages
  open_page: 'Open a URL and keep it open; returns page_id for click/scroll/screenshot/read_open_page and the devtools tools. Records network, console and JSON API responses from the first request. Closes after 10 min unused. Different page_ids run in parallel. One-off reading: read_smart. Fails with "프로필 사용 중" if the login profile is held elsewhere.',
  read_open_page: 'Current text/links of an open page after clicks or scrolls; auto_scroll loads lazy content first.',
  click: 'Click by uid (from take_snapshot, best), x/y, selector or visible text. Returns after_click: what changed (url/title, element state, dialogs, messages, text) or no_visible_change.',
  scroll: 'Mouse wheel at x,y (default center); +delta_y scrolls down / zooms a map out; one notch ≈ 100. To load all lazy content: auto_scroll on read_smart / read_open_page.',
  screenshot: 'JPEG of the viewport, full page or one element (selector/uid); image coordinates match click x,y.',
  wait_for_human: 'After needs_human: tell the user which site, then call this. The page opens in a plain window for the user (bot check/login); when the user closes it the page continues under the same page_id (until_text checks a logged-in marker). On timeout call again.',
  go_back: 'Go back in an open page\'s history.',
  close_page: 'Close an open page.',
  // API, data
  api_responses: 'JSON API responses an open page received (from the first request): url, status, size, JSON shape; index for one body, url_filter to filter. Quickest way to find the data API of a page, then page_fetch. Cookie/token values masked.',
  page_fetch: 'Call an API with fetch() inside an open page using its login cookies (e.g. found with api_responses). Same site only; GET/HEAD by default, POST/PUT/PATCH/DELETE need allow_write:true. JSON comes back parsed and unmasked. Use this instead of fetch in evaluate_script.',
  // devtools engine
  evaluate_script: 'Run JavaScript in the page (result must be JSON-serializable). API calls: page_fetch; framework JSON: read_smart embedded_json.' + DT,
  list_network_requests: 'All network requests since the last navigation (any type, headers). JSON API bodies: api_responses is quicker.' + DT,
  get_network_request: 'One network request by reqid: headers and body. JSON bodies: api_responses (index) is quicker.' + DT,
  list_console_messages: 'Console messages since the last navigation.' + DT,
  get_console_message: 'One console message by msgid.' + DT,
  navigate_page: 'Go to a URL, back, forward or reload (no bot-check detection; for a new URL prefer open_page).' + DT,
  take_snapshot: 'Text snapshot of the page (a11y tree) with element uids; use the latest one, prefer it over screenshots.' + DT,
  wait_for: 'Wait until any of the given texts appears.' + DT,
  fill: 'Type into an input/textarea or pick a <select> option.' + DT,
  fill_form: 'Fill several form elements at once (prefer over many fill/click calls).' + DT,
  type_text: 'Type into the focused input (optional submitKey).' + DT,
  press_key: 'Press a key or combination (e.g. Enter, Control+A).' + DT,
  hover: 'Hover over an element.' + DT,
  drag: 'Drag an element onto another.' + DT,
  upload_file: 'Upload local file(s) through a file input.' + DT,
  handle_dialog: 'Accept or dismiss an open browser dialog.' + DT,
  resize_page: 'Resize the page window.' + DT,
};

// Parameter descriptions shortened (tool.param; '*.param' for every tool).
export const PDESC = {
  '*.includeSnapshot': 'Include a snapshot in the result (default false)',
  '*.uid': 'Element uid from take_snapshot',
  '*.pageSize': 'Max items (default all)',
  '*.pageIdx': 'Page number, 0-based',
  '*.timeout': 'Max wait ms (0 = default)',
  'read_smart.fast': 'One HTTP request, no page scripts; falls back to rendering when blocked/thin (fast.used). Script-filled values can be missing',
  'read_smart.embedded_json': 'Framework JSON in the page (Next, Nuxt, Apollo, __INITIAL_STATE__...); large → shape only, pick with json_paths/json_find. Standard metadata: structured_data',
  'read_smart.auto_scroll': 'Scroll to the bottom first for lazy content (renders, max ~15 s, reached_limit). Step by step: open_page + scroll',
  'read_smart.structured_data': 'JSON-LD/microdata/meta, place info, dates. Framework state: embedded_json',
  'read_smart.json_paths': 'Paths in the embedded JSON, e.g. ["next_data.props.pageProps"]',
  'read_smart.json_find': 'Keyword: paths whose key or value contains it',
  'read_smart.include_hidden': 'Also return text hidden from people (hidden_text), separately',
  'read_smart.selector': 'CSS selector to read only that part',
  'read_smart.include_links': 'Also return the page links',
  'read_smart.headers': 'Extra request headers (Referer, Accept-Language...)',
  'read_smart.mode': 'main (default): main content; all: all visible text',
  'web_search.fetch_top': 'Also read the top N results (default 0); blocked ones retried directly / other language edition',
  'web_search.structured_data': 'For fetched results: place info and dates',
  'web_search.read': 'For fetched results: main (default), all or none',
  'web_search.section': 'For fetched results: only parts about these words',
  'web_search.site': 'Only these sites, e.g. "tabelog.com"',
  'web_search.prefer_detail': 'Rank detail pages above list/search pages',
  'save_places.urls': 'Google Maps place links',
  'save_places.list': 'List name exactly as in Maps',
  'fetch_images.time_budget_ms': 'Stop starting downloads after this (default 150000); call again for the rest',
  'wait_for_human.until_text': 'Also wait for this text (e.g. "로그아웃")',
  'page_fetch.url': 'Absolute URL or path relative to the open page',
  'read_open_page.auto_scroll': 'Scroll to the bottom first for lazy content (max ~15 s)',
  'evaluate_script.function': 'JavaScript function, e.g. () => document.title or (el) => el.innerText with args',
  'evaluate_script.format': '"function" (default, supports args) or "script"',
  'evaluate_script.sourcePath': 'Local .js file instead of function',
  'evaluate_script.args': 'Element uids passed to the function',
  'evaluate_script.filePath': 'Save the output to this file instead',
  'evaluate_script.dialogAction': 'Dialogs during the run: accept (default), dismiss or prompt text',
  'evaluate_script.waitForStableDom': 'Wait for the DOM to settle (default true; false when only reading)',
  'get_network_request.reqid': 'reqid from list_network_requests',
  'get_network_request.requestFilePath': 'Save the request body to this file',
  'get_network_request.responseFilePath': 'Save the response body to this file',
  'list_network_requests.resourceTypes': 'Only these resource types',
  'list_network_requests.includePreservedRequests': 'Include the last 3 navigations',
  'list_console_messages.types': 'Only these message types',
  'list_console_messages.includePreservedMessages': 'Include the last 3 navigations',
  'list_console_messages.includeStackTraces': 'Include stack traces (bigger output)',
  'list_console_messages.serviceWorkerId': 'Only this service worker',
  'get_console_message.msgid': 'msgid from list_console_messages',
  'navigate_page.type': 'url, back, forward or reload',
  'navigate_page.handleBeforeUnload': 'beforeunload dialogs: accept (default) or decline',
  'navigate_page.initScript': 'Script run on each new document before page scripts',
  'take_snapshot.verbose': 'Full a11y tree (default false)',
  'take_snapshot.filePath': 'Save the snapshot to this file instead',
  'press_key.key': 'Key or combination, e.g. "Enter", "Control+A"',
  'fill.value': 'Value; "true"/"false" for checkboxes',
  'upload_file.uid': 'uid of the file input (or element opening a file chooser)',
  'upload_file.filePaths': 'Local file paths',
  'type_text.submitKey': 'Key to press after typing, e.g. "Enter"',
  'wait_for.text': 'Texts; resolves when any appears',
};

function shortenProps(name, props) {
  for (const [k, v] of Object.entries(props || {})) {
    const d = PDESC[`${name}.${k}`] ?? PDESC[`*.${k}`];
    if (d !== undefined && v && typeof v === 'object' && v.description) v.description = d;
    if (v?.items?.properties) shortenProps(name, v.items.properties);
  }
}

// Returns a shortened copy of one tool entry from tools/list.
export function shortTool(t) {
  const out = { ...t, inputSchema: JSON.parse(JSON.stringify(t.inputSchema || {})) };
  if (DESC[t.name]) out.description = DESC[t.name];
  delete out.inputSchema.$schema;
  shortenProps(t.name, out.inputSchema.properties);
  return out;
}
