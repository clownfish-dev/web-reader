# web-reader 0.9.4

Claude Code가 웹페이지를 읽고 조작할 때 쓰는 MCP 서버예요. PC에 깔린 Edge와 따로 노는 **전용 Edge 사본**으로 페이지를 열기 때문에, 평소 쓰는 브라우저·로그인과 섞이지 않아요. Cloudflare·403으로 막히는 사이트도 실제 브라우저로 열어 읽고, 사람 확인이 필요하면 창을 띄워 사용자가 한 번 풀면 그 뒤로는 로그인이 유지돼요.

- 읽기: 주소 하나 읽기, 웹 검색, 게시글·댓글 수집, 이미지 모으기, 지도·숙소·경로 검색
- 조작: 페이지 열기 → 요소 목록 → 클릭·입력 → 결과 확인
- API: 페이지가 받은 JSON 응답 보기, 로그인된 페이지 안에서 API 호출
- chrome-devtools 도구 포함(따로 등록할 필요 없음)

## 준비물
- Windows 10/11
- [Node.js](https://nodejs.org/) 20 이상
- Microsoft Edge (Windows에 기본으로 깔려 있어요)
- [Claude Code](https://claude.com/claude-code)

## 1. 설치
아래는 모두 `web-reader` 폴더에서 명령 프롬프트(cmd)로 실행해요. 폴더 위치는 어디든 괜찮아요.

```
npm install
cd devtools
npm install
cd ..
```

### 전용 Edge 사본 만들기
PC에 깔린 Edge를 `edge` 폴더로 복사해요. 이 사본은 자동 업데이트를 받지 않아서 평소 Edge가 업데이트돼도 web-reader 쪽은 그대로예요.

```
robocopy "C:\Program Files (x86)\Microsoft\Edge\Application" edge /MIR /R:1 /W:1
icacls edge /grant *S-1-15-2-1:(OI)(CI)(RX) *S-1-15-2-2:(OI)(CI)(RX) /T /Q
```

- 두 번째 줄(권한 주기)을 빼먹으면 페이지가 안 떠요.
- 사본을 새 버전으로 바꾸고 싶으면 같은 두 줄을 다시 실행하면 돼요.
- `edge` 폴더가 없으면 PC에 깔린 Edge를 그대로 써요.

## 2. Claude Code에 등록
```
node register-global.js --windowed
```
`~/.claude.json`에 web-reader를 등록해요(원래 파일은 먼저 백업). 이후 새로 여는 Claude Code 세션이면 어느 프로젝트에서든 쓸 수 있어요.

- `--windowed`: 필요할 때 브라우저 창을 띄울 수 있어요(보통 이걸로).
- 옵션 없이: 창을 절대 띄우지 않아요(화면 없는 서버 PC용).

등록 후 Claude Code를 새로 열고 `/mcp`에서 web-reader가 보이면 성공이에요.

### 다른 MCP 프로그램에서 쓰기
MCP를 지원하는 프로그램이면 같은 방식으로 쓸 수 있어요. 실행 명령은 `node`, 인자는 `server.mjs`의 전체 경로예요. 예를 들어 Codex CLI는 이렇게 등록해요(경로는 web-reader 폴더 위치로 바꿔요).

```
codex mcp add web-reader -- node C:\web-reader\server.mjs
```

또는 `~/.codex/config.toml`에 직접 적어도 돼요.

```toml
[mcp_servers.web-reader]
command = "node"
args = ["C:\\web-reader\\server.mjs"]
```

창을 띄우지 않게 하려면 환경 변수 `WEB_READER_HEADLESS_ONLY=1`을 넣어요(Codex CLI는 `--env WEB_READER_HEADLESS_ONLY=1`).

## 3. 첫 로그인
로그인이 필요한 사이트나 봇 확인 화면을 만나면 도구 결과에 `needs_human`이 와요. 그러면 Claude가 `wait_for_human`을 부르고, 화면에 일반 Edge 창이 떠요. 그 창에서 로그인·확인을 끝내고 **창을 닫으면** 하던 작업이 이어져요.

로그인 정보는 web-reader 폴더 안의 `profile`, `profile-headed` 폴더에 저장돼서 다음부터는 다시 로그인하지 않아도 돼요. 사이트마다 한 번이면 돼요.

> `profile*` 폴더에는 쿠키·로그인 정보가 들어 있어요. 다른 사람에게 주거나 올리지 마세요.

## 4. 도구 목록
Claude가 알아서 골라 쓰지만, 이름을 알아 두면 "read_smart로 읽어 줘"처럼 지정할 수 있어요.

| 분류 | 도구 |
|---|---|
| 읽기 | `read_smart`(주소 하나 읽기, 보통 이것), `web_search`, `get_links`, `check_domains`, `collect_post` |
| 검색 | `hotel_search`(구글 호텔), `transit_route`(구글 지도 경로), `place_lookup`(장소 이름 → 구글 평점·리뷰 수·좌표·폐업) |
| 이미지·지도 | `page_images`, `find_place_photos`, `fetch_images`, `contact_sheet`, `static_map`, `save_places`(구글 지도 저장 목록) |
| 열린 페이지 | `open_page`, `read_open_page`, `click`, `scroll`, `screenshot`, `wait_for_human`, `go_back`, `close_page` |
| API | `api_responses`, `page_fetch` |
| devtools | `take_snapshot`, `fill`, `fill_form`, `type_text`, `press_key`, `hover`, `drag`, `upload_file`, `handle_dialog`, `wait_for`, `navigate_page`, `resize_page`, `evaluate_script`, `list_network_requests`, `get_network_request`, `list_console_messages`, `get_console_message` |

### 자주 쓰는 방법
- **주소 읽기**: `read_smart`에 `fast:true`면 HTTP 한 번으로 빠르게 읽고, 막히거나 내용이 부족하면 브라우저 렌더링으로 알아서 넘어가요. 가격·재고처럼 스크립트가 채우는 값은 `fast` 없이.
- **페이지 속 JSON**: `read_smart`에 `embedded_json:true`(Next.js·Nuxt 등 데이터). 크면 구조만 오고 `json_paths`·`json_find`로 골라 받아요.
- **끝까지 내려야 나오는 내용**: `auto_scroll:true`.
- **조작**: `open_page` → `take_snapshot`(요소마다 uid) → uid로 `click`/`fill`/`press_key` → `read_open_page`로 확인 → `close_page`. `click`은 누른 뒤 바뀐 내용을 `after_click`으로 같이 돌려줘요.
- **여러 주소 한 번에**: `read_smart`에 `urls`(최대 50개). 탭은 사이트마다 5개로 시작해 잘되면 최대 10개까지 늘고, 403·확인 화면·429가 나오면 2개로 줄인 뒤 막힌 주소를 한 번 더 읽어요.
- **여러 탭 동시**: 읽기 도구는 읽기 전용 표시(`readOnlyHint`)가 있어서 Claude Code가 한 메시지 안의 여러 호출을 동시에 보내요. 조작이 필요하면 `open_page`를 여러 개 열고 page_id마다 따로 불러요.
- **구글 장소 정보**: `place_lookup`(이름 + `near` 좌표). 체인점은 가까운 지점을 골라요. 구글 응답 형식에 기대므로 바뀌면 빈 결과가 날 수 있어요.
- **API 찾기·부르기**: `open_page` → `api_responses`(받은 JSON 목록) → `page_fetch`(같은 사이트만, GET 기본, POST 등 쓰기는 `allow_write:true`).
- 도구 결과는 웹에서 온 데이터라, 그 안에 적힌 지시는 따르지 않아요.

## 설정 (선택)
`web-reader.config.example.json`을 `web-reader.config.json`으로 복사해서 고쳐요. 없으면 모두 기본값이에요.

| 항목 | 뜻 |
|---|---|
| `shared_window` | `true`면 창 있는 브라우저를 사용자도 직접 쓰는 일반 창으로 띄우고, 작업 표시줄에 주황 고양이 아이콘을 붙여 평소 Edge와 구분해요. |
| `keep_browser` | `true`면 세션이 끝나도 로그인 브라우저를 켜 둬요(항상 켜 두는 PC용). |
| `devtools_extra` | 숨긴 devtools 도구 다시 켜기. 예: `["lighthouse_audit"]`, 전부는 `["all"]`. 숨긴 도구: performance_start_trace/stop_trace/analyze_insight, lighthouse_audit, take_heapsnapshot, screencast_start/stop, emulate, get_css_styles |

설정을 바꾸면 Claude Code 세션을 새로 열어야 적용돼요.

## 명령줄 시험
Claude 없이 도구를 바로 불러 볼 수 있어요.
```
node call.mjs list
node call.mjs read_smart "{\"url\":\"https://example.com\",\"fast\":true}"
```

## 폴더 구조
| 파일 | 내용 |
|---|---|
| `server.mjs` | MCP 서버 본체(브라우저 관리, 열린 페이지·조작 도구) |
| `search.mjs` | 읽기·검색·이미지·지도 도구 |
| `tool-text.mjs` | Claude가 보는 짧은 도구 설명 |
| `register-global.js` | Claude Code 등록 |
| `call.mjs` | 명령줄 시험 |
| `devtools/` | chrome-devtools 도구 엔진 설정 |
| `vendor/` | 본문 추출(Readability), 지도(Leaflet) |
| `tools/window-icon.ps1`, `tools/web-reader-cat.*` | 작업 표시줄 고양이 아이콘 |
| `tools/cookie-count.mjs` | 떠 있는 브라우저의 쿠키 수 확인: `node tools\cookie-count.mjs profile-headed` |

실행하면 생기는 폴더: `edge/`(전용 Edge), `profile*/`(로그인 정보), `run/`(실행 상태), `data/`(내려받은 파일·캐시). 모두 `.gitignore`에 들어 있어요.
