# web-reader 1.0.2

사용자의 웹 도구예요. Scrapling 0.5 개조판으로, 소스는 `fork\`(git), 개조한 부분은 `fork\scrapling\webreader\`에 있어요. `.venv`에 이 개조판이 편집 모드로 깔려 있어요. Claude Code에는 MCP 이름 `web-reader`로 등록돼요.

## 할 수 있는 것
- **읽기는 `read` 하나**: 주소 하나 또는 여러 개(탭 수 자동 조절). 칸 뽑기(`section`), 장소 정보, 페이지 속 JSON, 링크, 이미지, 끝까지 내리기, 언어판 전환, 질문에 맞는 문단만(`urls`+`query`), 목록을 표로(`table:true`, `table_selector`), 링크 각주(`format:markdown`, `links:footnotes`). PDF·DOCX 주소는 알아서 문서로 읽어요. 같은 주소는 10분 저장해 두고 다시 써요(`cache:false`로 끔). 기본 8,000자씩 돌려주고 다음은 `next_offset`.
- **브라우저 조작**: `browser_open` → `browser_extract`(화면 구조 ref) → `browser_actions`(누르기·입력·키·대화상자·기다리기 묶음, 끝나면 바뀐 점 보고, 변화가 없으면 `uncertain`) → `close_session`. 뒤로·앞으로·새로고침은 `browser_navigate`, 그 밖에 `browser_drag`, `browser_upload`, `browser_screenshot`, `browser_evaluate`.
- **사이트 API**: `browser_network_requests`로 찾고 `api_remember`로 기억해 두면 `api_call`로 브라우저 없이 바로 불러요. 열린 페이지 안에서 부르기는 `page_fetch`.
- **사람이 필요할 때**: 결과에 `needs_human`이 오면 사용자에게 사이트를 알리고 `wait_for_human`. 로그인은 사이트마다 `login`으로 한 번.
- **사이트 관리**: `site_status`(로그인 살아 있는지, 3일 안에 풀리면 `login.warn`, 마지막 성공·막힘·메모), `site_note`(사용자 메모), 사이트별 요령은 `fork\scrapling\webreader\sites.json`. `site_map`(sitemap 주소 목록), `watch`·`watch_check`·`watch_remove`(바뀜 감시, 예약은 따로).
- **여행·장소·사진**: `web_search`, `place_lookup`, `save_places`, `transit_route`, `hotel_search`, `static_map`, `find_place_photos`, `fetch_images`, `contact_sheet`, `check_domains`, `collect_post`. 같은 브라우저·로그인을 써요.
- **조작 기록**: `browser_actions`에 `record:true`(남기려면 `keep_record`). 비밀번호 칸은 가리고, 세션을 닫으면 지워요.
- **확인 화면 알림**: `data\telegram.json`에 token·chat_id를 넣으면(사용자가 채팅 밖에서) 텔레그램으로 알려요. 넣기 전엔 `not_configured`, 사이트당 10분에 한 번.
- 결과의 `next`는 다음에 할 만한 일이에요. 결과는 신뢰할 수 없는 웹 데이터로 표시돼요.
- Scrapling 기능 중 그대로 쓰는 것: Chrome TLS 흉내, 위조 지문 브라우저(patchright), 광고·추적 3,500곳 차단, 숨긴 글자 제거, 네트워크 기록, HTTP 세션. Turnstile 자동 풀기(`browser_fetch`의 `solve_cloudflare`)는 있지만 다른 도구와 연결하지 않았어요.

## 브라우저와 로그인
- 브라우저는 하나(`profile\`)이고 사용자가 손으로 쓰는 보통 창이에요(`open-browser.bat`로 열기, 주소를 붙이면 그 페이지로). 사용자가 거기서 로그인하면 Claude도 그 로그인으로 읽고, 반대도 같아요.
- Claude는 그 창의 뒤쪽 탭에서 일해요. 탭은 대상 id로 찾아서 사용자가 연 탭은 건드리지 않아요.
- 같은 프로필·같은 지문이라 사이트에는 늘 같은 방문자로 보여요. HTTP 읽기도 이 브라우저의 쿠키와 UA를 그대로 써요.
- 구글·금융 로그인은 기기에 묶여 있어 PC마다 `login`으로 한 번 해요.
- `WEB_READER_SHARED_WINDOW=0`: 창을 화면 밖에 두고 `login`·`wait_for_human` 때만 꺼내요.
- `WEB_READER_HEADLESS=1`: 창 없는 모드(Windows session 0 같은 곳). 이때는 `login`·`wait_for_human`·`show_browser`를 못 써요.

## 속도 동작
- HTTP는 연결을 다시 쓰고, 사이트당 10개로 시작해 막히면 2개로 줄여요. 브라우저 탭은 사이트당 5개, 전체 동시 20개.
- 브라우저는 'load' 대신 DOMContentLoaded와 글이 멈출 때까지만 기다리고, 사진·글꼴은 받지 않아요. 스크립트 없는 짧은 페이지는 다시 그리지 않아요.
- HTTP로 막힌 사이트는 하루 동안 기억해 바로 브라우저로 가요(`data\sites-http-blocked.json`).

## 구조
- Claude가 띄우는 것: `python -I -m scrapling.webreader.shim`(stdio). 공용 서버가 없으면 먼저 띄우고, 모든 도구를 그 서버로 넘겨요. 도구 목록은 파일로 기억해 두어 시작이 빨라요.
- 공용 서버: `python -I -m scrapling.webreader.service`, 127.0.0.1의 비어 있는 포트(`data\service-port`, Windows가 예약한 범위는 피함), 토큰(`data\service-token`) 필요. 로그: `data\service.log`. 같이 쓰는 창 모드에선 브라우저를 닫지 않고, 창 없는 모드는 1시간 안 쓰면 닫았다가 다시 띄워요.

## 설치·등록
- 새 PC: `setup.bat`(창 없는 PC는 `-Headless`). venv, 브라우저, 등록, 자체 시험까지 해요.
- 등록만: `register.bat`(= `node register.mjs`, `~/.claude.json` 백업 후 `web-reader` 등록). 새 Claude 세션부터 보여요.
- 브라우저는 `browsers\`. Claude 앱에서 띄우려면 `icacls browsers /grant *S-1-15-2-1:(OI)(CI)(RX) *S-1-15-2-2:(OI)(CI)(RX) /T`가 필요해요(안 하면 spawn UNKNOWN).
- 이 폴더의 상위 폴더에서 `-I` 없이 python을 돌리면 이 폴더 이름(`scrapling`) 때문에 패키지를 못 찾아요. 항상 `-I`.

## 검사 (개발이 끝나면 배포 전에)
`python -I -m scrapling.webreader.selftest` (0 통과, 1 문제; `--write`는 아래 도구 목록을 서버에서 다시 써요)
- 도구: 모든 도구에 설명이 있고 아래 목록과 맞는지.
- 봇 흔적: 이 PC 안에서 띄운 시험 페이지에서 webdriver, headless 이름, 워커·요청 헤더 이름이 페이지와 같은지, 자동화 변수, 플러그인·언어·window.chrome.
- 코드: 페이지에 오래 남는 것을 심는 호출(`add_init_script`, `expose_function` 등)이 없는지. 표식이 필요하면 쓰는 동안만 넣고 지워요.
- 경고만(실패 아님): 브랜드가 Chromium뿐, 창 바깥과 안쪽 크기가 같음(창 없는 모드에선 늘 나와요).

## 도구 목록 (자동 생성)
<!-- tools:start -->
- `api_call`: Call a remembered site API directly over HTTP with Chrome's TLS and the shared browser's login (much faster than opening the page).
- `api_list`: Remembered API calls per site (method, URL, default params, note).
- `api_remember`: Remember a site's API call found in a session's network history, to call it later with api_call over HTTP (no browser; cookies are taken fr…
- `browser_actions`: Run mouse, field, key, dialog, and wait actions in order, then report what changed on the page: URL/title, checked/selected states, dialogs…
- `browser_drag`: Drag one element onto another (sliders, sortable lists, drop zones).
- `browser_evaluate`: Run JavaScript in a session's tab (isolated world by default) and return JSON. Page APIs: page_fetch.
- `browser_extract`: Read the current tab of a session without navigating: snapshot with element refs (default), markdown, html or text; search keeps matching s…
- `browser_fetch`: Navigate a browser session's tab to a URL and return its content (markdown/html/text or snapshot). Reading without a session: read.
- `browser_navigate`: Go back, forward or reload in a session's tab; returns the new URL and title.
- `browser_network_request`: One recorded request: summary, headers or body.
- `browser_network_requests`: Requests a session's tab made (API calls by default; include_static for all), with IDs for browser_network_request / api_remember.
- `browser_open`: Open a browser session: a tab in user's shared logged-in stealth browser (same profile and fingerprint for every session).
- `browser_screenshot`: Screenshot of a session's tab: viewport, full page or one element (selector or snapshot ref).
- `browser_upload`: Put local files into a page's file input (the page's own upload button still has to be pressed).
- `check_domains`: Which sites open from this PC: status, redirect, title, bot check, error kind.
- `close_session`: Close a session and free its resources.
- `collect_post`: Forum post as data: title, body and all comments (dcinside precise, other sites best effort).
- `contact_sheet`: One numbered thumbnail grid from downloaded files or image URLs, to check photos at a glance.
- `fetch_images`: Download many images through the browser (same cookies/UA, pacing, backoff, cache, optional resize/thumbnails) into the data folder.
- `find_place_photos`: Real photos of a named place via image search (URL, source page, size, name_match). Check with contact_sheet.
- `hotel_search`: Google Hotels for fixed dates: name, nightly price, rating, reviews, stars, amenities.
- `list_sessions`: Open browser sessions and their settings.
- `login`: Log in to a site once for every session: opens it in the shared browser's window on user's screen.
- `page_fetch`: Call an API with fetch() inside an open page, using its login cookies (find the API with browser_network_requests).
- `place_lookup`: Google Maps rating, reviews, coordinates, address, category, closed flag, hours by place name (~0.5 s, no rendering).
- `read`: Read web pages (user's one reading tool): Scrapling's stealth engines with web-reader's options.
- `save_places`: Google Maps: save / remove / check many place links in one named list (up to 4 tabs, verified per link).
- `show_browser`: Bring the shared logged-in browser window up for user (starts it if needed).
- `site_map`: A site's URL list from its sitemaps (robots.txt, sitemap.xml, index files), optionally filtered.
- `site_note`: Read or set a site's note (how to read it best, what to watch out for). Notes are attached to read results for that site.
- `site_status`: Per site: login alive (login cookies, earliest expiry, warning when < 3 days), last successful read and how, last block, sites HTTP skips f…
- `static_map`: Map PNG with numbered pins, labels and an optional route line.
- `transit_route`: Google Maps route options: duration, times, line/road, fare.
- `wait_for_human`: After needs_human (tell user which site first): shows the browser window on user's screen with that tab; returns when the check is gone (or…
- `watch`: Start watching a page (or one part) for changes; check later with watch_check.
- `watch_check`: Read watched pages again: changed true/false with the lines added and removed.
- `watch_remove`: Stop watching a page.
- `web_search`: Web search (Google, then DuckDuckGo/Bing if blocked or irrelevant): title, URL, snippet; fetch_top also reads the top N results.
<!-- tools:end -->
