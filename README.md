# free-llm-coder

무료 웹 LLM 서비스(ChatGPT, Gemini, Qwen, Grok, DeepSeek, GLM, Kimi — 그리고
config로 추가하는 어떤 채팅 사이트든)를 Playwright 브라우저 자동화로 구동하는
도구입니다. 한 서비스가 사용량 한도에 걸리면 다음 서비스로 자동 전환하면서
**대화 맥락을 인수인계**하므로, 유료 구독 없이 여러 무료 티어를 하나의 AI처럼
이어서 쓸 수 있습니다.

두 가지 방식으로 사용합니다:

1. **터미널 채팅** (`flc chat`) — 일반 Q&A 또는 프로젝트 컨텍스트를 아는 코딩 어시스턴트
2. **OpenAI 호환 API 서버** (`flc serve`) — Open WebUI, LM Studio 같은 기존 채팅 UI에 연결

> ⚠️ 서드파티 채팅 UI를 자동화하는 도구입니다. 각 서비스의 약관을 확인하세요.
> 사이트 UI가 바뀌면 선택자가 깨질 수 있고(→ `flc doctor`로 진단), 로그인은
> 실제 Chrome 창에서 직접 합니다(세션은 디스크에 저장되어 재사용).

---

## 설치

```bash
pip install -e .
playwright install chrome        # 자동화에 쓸 브라우저 설치
```

개발(테스트 실행) 시:

```bash
pip install -e ".[dev]"
pytest -q
```

> 참고: 명령 이름은 `flc`입니다. (`fc`는 zsh 내장 명령과 충돌하므로
> 쓰려면 `command fc`로 실행해야 합니다.)

## 최초 설정

```bash
flc init          # ~/.free-llm-coder/config.yaml 생성 (chat 첫 실행 시 자동 생성되기도 함)
flc login         # 등록된 서비스를 하나씩 차례로 로그인 (y/s/q로 진행·건너뛰기·중단)
flc login qwen    # 또는 특정 서비스만
flc status        # 서비스별 로그인/세션 상태 확인
```

- 로그인 세션은 `~/.free-llm-coder/user_data/<서비스>/`에 저장되어 **서비스당
  한 번만** 로그인하면 됩니다 (만료 전까지 유효).
- `flc login`은 headless 설정과 무관하게 항상 창이 뜹니다 — 직접 로그인해야 하니까요.
- `flc status`는 각 서비스를 창 없이 열어 다음 중 하나로 보고합니다:
  **ready**(사용 가능) / **login needed**(재로그인 필요) / **human verification
  pending**(사람 인증 대기 — 창을 띄워 직접 완료) / **never logged in** /
  **in use by another process**(chat/serve가 프로필 점유 중).

## 명령어 한눈에 보기

| 명령 | 역할 |
|---|---|
| `flc init` | 기본 config 생성 |
| `flc login [서비스]` | 로그인 (인자 없으면 전체 서비스 마법사) |
| `flc status [-s 서비스]` | 서비스별 세션 상태 점검 |
| `flc chat` | 터미널 대화 (아래 참조) |
| `flc serve` | OpenAI 호환 API 서버 (아래 참조) |
| `flc doctor [-s 서비스]` | 선택자 생존 점검 — 서비스가 안 될 때 첫 번째로 실행 |

---

## 사용법 1: 터미널 채팅

```bash
flc chat -m chat                 # 일반 챗봇처럼 (질문이 지침 없이 원문 그대로 전달됨)
flc chat -d ~/my-project         # 코딩 모드(기본): 프로젝트 파일이 컨텍스트로 전달됨
```

| 플래그 | 효과 |
|---|---|
| `-m, --mode <code\|chat>` | `code`(기본): 코딩 어시스턴트 — 프로젝트 컨텍스트 + 파일/명령 블록 처리. `chat`: 일반 Q&A |
| `-d, --dir <경로>` | 코딩 모드에서 분석할 프로젝트 디렉터리 |
| `-s, --service <이름>` | 우선순위 무시하고 이 서비스부터 시도 |
| `--headless / --headful` | 브라우저 창 숨김/표시 (config보다 우선) |
| `--dry-run` | 파일 쓰기/명령 실행을 미리보기만 |
| `-v, --verbose` | DEBUG 로그를 콘솔에도 표시 |

프롬프트에는 **다음에 답할 서비스(와 알려진 경우 모델)** 가 표시됩니다:
`You (qwen · Qwen3.8-Max):`

### 채팅 안에서 쓰는 명령

| 명령 | 효과 |
|---|---|
| `/switch [서비스]` | 다음 질문부터 다른 서비스가 답변 (이름 생략 시 다음 순위). 대화 인수인계 자동 — 로테이션 테스트에도 유용 |
| `/status` | 실행 중인 세션의 서비스 가용성·쿨다운 상태 |
| `/models` | 현재 서비스의 모델 목록을 **실시간으로** 읽음 (하드코딩 없음 — 새 모델 출시/구모델 삭제 자동 반영) |
| `/model <이름>` | 모델 선택 (부분 일치 지원, 예: `/model 3.8-max`) |
| `/modes`, `/mode <이름>` | 서비스의 모드/도구 메뉴 (예: Qwen의 웹 검색·심층 리서치) |
| `/new`, `/reset` | 새 대화 시작 (코딩 모드면 다음 턴에 전체 컨텍스트 재전송) |
| `exit`, `quit` | 종료 (Ctrl+C/Ctrl+D도 동일) |

### 코딩 모드의 응답 처리

- 첫 턴에 프로젝트 파일 전송(`max_files`/`max_chars` 예산 내), 이후 턴은
  변경된 파일만 — **서비스별로 따로 추적**되므로 로테이션된 새 서비스도
  전체 컨텍스트를 받습니다.
- 응답의 ` ```file:상대/경로 ``` ` 블록 → diff 미리보기 후
  `[y]es/[n]o/[a]ll/[q]uit`로 적용. 프로젝트 밖 경로는 거부.
- ` ```bash ``` ` 블록 → 위험도 검사(파괴적 명령은 차단) 후 하나씩 확인받고
  실행 (5분 타임아웃).

---

## 사용법 2: API 서버 — Open WebUI / LM Studio 연결

채팅 UI를 직접 만드는 대신 OpenAI API 형태로 노출해서 기존 프런트엔드를 씁니다.

```bash
flc serve --headless             # http://127.0.0.1:8000/v1
flc serve --host 0.0.0.0 -p 9000 # 다른 기기에서 접속 시
```

이 터미널은 **켜둔 채로 유지**합니다(끄면 서버 종료). 동작 확인:

```bash
curl http://127.0.0.1:8000/v1/models
```

### model 필드로 서비스/모델 선택

| model 값 | 효과 |
|---|---|
| `auto` | 우선순위 순서 + 한도 시 자동 로테이션 |
| `qwen`, `gemini`, ... | 해당 서비스로 고정 |
| `qwen/Qwen3.8-Max` | 해당 서비스에서 그 모델까지 선택 |

각 서비스를 처음 사용한 뒤에는 그 서비스의 구체적 모델들이 실시간 수집되어
`GET /v1/models` 목록에 `qwen/Qwen3.8-Max` 형태로 추가됩니다.

### Open WebUI

관리자 설정 → 연결(Connections) → OpenAI API 추가:
URL `http://127.0.0.1:8000/v1`, API 키는 아무 값. 모델 목록에 서비스들이 나타납니다.

### LM Studio

1. 플러그인 설치: [ankh/openai-compat-endpoint](https://lmstudio.ai/ankh/openai-compat-endpoint)
   ("Use in LM Studio" 버튼)
2. 상단 모델 선택에서 플러그인 로드 → 채팅을 하나 열고 → **우측 사이드바
   토글**을 열면 플러그인 설정란이 나옵니다:
   - Base URL: `http://127.0.0.1:8000/v1` (`/v1` 포함)
   - API Key: 아무 값 — ⚠️ **반드시 영문으로** (한글이 들어가면
     "Cannot convert argument to a ByteString" 오류)
   - Model: `auto` 또는 서비스명
3. 서비스를 바꾸려면 이 Model 칸을 수정합니다. `/switch` 같은 슬래시 명령은
   터미널 채팅 전용이라 여기서는 일반 메시지로 전달되어 버립니다.

### 서버 동작 특성

- 요청은 **한 번에 하나씩** 처리됩니다 — 실제 브라우저가 실제 채팅 페이지에
  타이핑하기 때문입니다.
- **첫 응답은 느립니다**(브라우저 기동, 1분 내외). 클라이언트가 타임아웃으로
  실패하면 잠시 후 다시 보내면 됩니다. 이후는 빠릅니다.
- 대화 기록은 웹 채팅이 보관하므로 평소엔 최신 질문만 전달됩니다. 서비스가
  중간에 교체되면(한도/서버 재시작) 못 본 턴의 대화록을 인수인계 받아
  이어집니다. 프런트엔드에서 다른 대화로 전환해도 중복 인수인계 한 번의
  비용만 있을 뿐 꼬이지 않습니다.
- 서버 실행 전에 필요한 서비스에 로그인해두세요 (`flc status`로 확인).

---

## 브라우저 창 숨기기 (headless)

```bash
flc chat -m chat --headless
flc serve --headless
```

영구 설정은 `config.yaml`에서 `browser.headless: true`. 드라이버가
`HeadlessChrome` User-Agent를 자동으로 마스킹합니다.

서비스마다 headless를 차단하는 강도가 다릅니다. 특정 서비스만 반복 실패하면
그 서비스만 창을 띄우는 혼합 운용을 하세요:

```yaml
browser:
  headless: true
services:
  - name: qwen
    headless: false     # 예: 사람 인증(슬라이더)이 뜨는 서비스만 창 표시
```

사람 인증(캡차)이 감지되면: headless에서는 몇 초 안에 포기하고 다음 서비스로
로테이션하고, headful에서는 직접 풀 수 있게 기다립니다. 인증 우회는 하지
않습니다.

## 문제 해결

| 증상 | 원인/조치 |
|---|---|
| 서비스가 응답 안 함 | `flc doctor -s <이름>` — NOT FOUND로 표시된 선택자를 config.yaml에서 수정 |
| "login page redirect" 오류 | `flc login <이름>` 재로그인 |
| "human-verification challenge" | 그 서비스만 `headless: false`로 창을 띄워 직접 완료 |
| "profile is already in use" | 같은 서비스 프로필은 프로세스 하나만 사용 가능 — chat/serve 중 하나를 종료 |
| 첫 응답이 매우 느림 | 정상 (브라우저 기동). 두 번째부터 빠름 |
| 답변이 왔는데 잘림/유실 의심 | 완성된 답변은 버리지 않도록 설계됨. 로그 확인 |

- 상세 로그: `~/.free-llm-coder/logs/free-llm-coder.log` (항상 DEBUG 기록)
- **실패 시점 스크린샷**: `~/.free-llm-coder/logs/snapshots/` — headless에서
  뭐가 떴는지(로그인 화면? 인증? UI 변경?) 눈으로 확인하는 가장 빠른 방법

## 동작 원리 (견고성 설계)

- **서비스별 영구 Chrome 세션**: 로그인이 실행 간 유지됨.
- **새 응답 감지**: 매 턴 새 응답 컨테이너가 나타난 뒤에만 읽으므로 이전 턴
  응답이 재사용될 수 없음. 완료 판정이 깨져도 텍스트가 15초간 정체되면
  답변으로 반환(안전망).
- **서킷 브레이커**: 3회 연속 실패한 서비스는 5분 쿨다운; 로테이션이
  건너뛰고 매 질문마다 재평가.
- **한도 감지의 증거 강도 구분**: 전용 선택자/서비스별 키워드(강함)는 즉시
  쿨다운, 범용 문구(약함)는 답변이 완성됐다면 무시 — **완성된 답변은 절대
  버리지 않음**.
- **대화 인수인계**: 프로그램이 자체 대화록을 보관하고 서비스별 열람 위치를
  추적 — 전환 시 못 본 턴만 전달(최신 우선, 예산 12,000자).
- **경로·명령 안전**: 파일 쓰기는 셸을 거치지 않고, 프로젝트 밖 경로는 거부;
  명령은 실행 전 패턴 검사.

## 설정 (config.yaml)

`~/.free-llm-coder/config.yaml`이 선택자·URL·우선순위·한도 키워드의 단일
소스입니다. 사이트가 바뀌면 코드 수정 없이 이 파일만 고치면 됩니다.

```yaml
browser:
  headless: false
  user_data_dir: /Users/you/.free-llm-coder/user_data
context:
  max_files: 25
  max_chars: 60000
  ignore_patterns: [".git", "__pycache__", "node_modules", "venv", "*.pyc"]
services:
  - name: chatgpt
    url: https://chatgpt.com
    priority: 1
    selectors:
      input_area: "#prompt-textarea"
      submit_button: "button[data-testid='send-button']"
      response_container: ".markdown"
      error_message: ".text-red-500"
      # new_chat_button: "..."        # 선택; 없으면 URL 새로고침으로 대체
    limit_indicators:
      selectors: []
      keywords: ["you've reached our limit", "message limit"]
```

### 새 서비스 추가 (코드 불필요)

config만으로 아무 채팅 사이트나 붙일 수 있습니다 (generic 드라이버):

```yaml
services:
  - name: mistral
    url: https://chat.mistral.ai
    priority: 8
    driver: generic
    selectors:
      input_area: "textarea"
      submit_button: "button[type='submit']"
      response_container: ".assistant-message"
      generating_indicator: ".stop-button"   # 선택: 생성 중에만 존재하는 요소
      model_menu: "button.model-picker"      # 선택: /models·/model 활성화
      model_item: "[role='menuitem']"        # 선택: role= 기본값으로 안 잡힐 때
    dismiss_selectors: ["text=쿠키 허용"]     # 선택: 클릭을 가로채는 배너 닫기
```

이후 `flc login mistral` → `flc doctor -s mistral`로 선택자 검증.
사이트에 특수 처리(예: Qwen의 Monaco 코드 블록)가 필요할 때만 Python
드라이버를 작성합니다 (`BaseDriver` 상속, `drivers/__init__.py`에 등록).

> 기본 제공되는 `glm`(chat.z.ai)과 `kimi`(kimi.com)는 generic 드라이버 +
> 추정 선택자입니다. 로그인 후 `flc doctor -s glm` / `-s kimi`로 확인하세요.
> ChatGPT 무료 계정에는 모델 픽커 UI가 없어 `/models`가 지원되지 않습니다
> (Plus 계정은 config 주석 참조).

## 프로젝트 구조

```
src/free_llm_coder/
  main.py            # Typer CLI: chat / serve / login / status / doctor / init
  server.py          # OpenAI 호환 FastAPI 서버 (flc serve)
  conversation.py    # 서비스 간 대화 인수인계 (대화록 + 서비스별 열람 추적)
  config_schema.py   # 기본값 + 검증 + 누락 서비스 주입
  writer.py          # ```file:<path>``` 파싱, 경로 안전성, diff 미리보기
  executor.py        # 셸 명령 위험도 분류 + 타임아웃 실행
  logging_setup.py   # 콘솔 + 파일 로거
  context/           # 프로젝트 스캔 + 초기/증분 프롬프트 구성 (서비스별 추적)
  drivers/           # 서비스별 Playwright 드라이버 + config 전용 generic 드라이버
  manager/           # ServiceManager (서킷 브레이커, 로테이션)
tests/               # pytest 스위트 (브라우저 불필요)
```

## 제한사항

- 웹 UI는 예고 없이 바뀌고 선택자는 깨집니다. `flc doctor`, 로그, 스냅숏,
  config가 유지보수 수단입니다.
- 봇 감지에 걸릴 수 있습니다. 명백한 자동화 흔적은 끄지만 완벽한 우회는
  불가능하며, 사람 인증이 뜨면 직접 풀어야 합니다.
- 매 질문은 해당 서비스 UI에서의 실제 행동이며 무료 티어 사용량에 그대로
  집계됩니다.
