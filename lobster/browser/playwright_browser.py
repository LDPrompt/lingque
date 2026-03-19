"""
🐦 灵雀 - 浏览器自动化 v3.0 (RPA 级别丝滑体验)

核心能力:
- CDP 连接真实 Chrome（绕过反爬检测）
- Playwright aria_snapshot 无障碍树快照 + 元素引用编号
- 失效引用自动恢复（操作失败自动重新快照重试）
- 多标签页管理
- Cookie 持久化（自动加载/保存登录状态）
- 智能元素定位（多策略重试）
- 导航控制（前进/后退/等待）

v3.0 RPA 增强（反爬绕过）:
- 🖱️ 鼠标平滑移动（贝塞尔曲线轨迹，绕过轨迹检测）
- ⌨️ 人类化输入（随机延迟逐字输入，绕过输入速度检测）
- 📜 平滑滚动（渐进式滚动，更自然）
"""

import asyncio
import json
import logging
import math
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx


# ==================== RPA 风格配置 ====================

class RPAConfig:
    """RPA 风格操作配置（核心目的：绕过反爬检测）"""
    enabled: bool = True                    # 是否启用 RPA 风格
    mouse_move_duration: float = 0.15       # 鼠标移动时长（秒）
    mouse_steps: int = 10                   # 鼠标移动步数
    type_delay_min: int = 20                # 输入最小延迟（毫秒）
    type_delay_max: int = 60                # 输入最大延迟（毫秒）
    click_delay: float = 0.03               # 点击前停顿（秒）
    scroll_smooth: bool = True              # 是否平滑滚动

    @classmethod
    def from_env(cls):
        """从环境变量加载配置"""
        cls.enabled = os.environ.get("RPA_MODE", "true").lower() == "true"
        cls.mouse_move_duration = float(os.environ.get("RPA_MOUSE_DURATION", "0.15"))
        cls.type_delay_min = int(os.environ.get("RPA_TYPE_DELAY_MIN", "20"))
        cls.type_delay_max = int(os.environ.get("RPA_TYPE_DELAY_MAX", "60"))


# ==================== RPA 工具函数 ====================

def _bezier_curve(t: float, p0: float, p1: float, p2: float, p3: float) -> float:
    """三次贝塞尔曲线计算"""
    return (1-t)**3 * p0 + 3*(1-t)**2*t * p1 + 3*(1-t)*t**2 * p2 + t**3 * p3


def _generate_mouse_path(start_x: float, start_y: float, end_x: float, end_y: float, steps: int = 20) -> list:
    """
    生成人类化的鼠标移动路径（贝塞尔曲线 + 随机抖动）
    
    模拟人类鼠标移动特点：
    1. 不是直线，而是略带弧度
    2. 有轻微的随机抖动
    3. 速度先快后慢（缓动效果）
    """
    path = []
    
    # 计算控制点（产生自然的弧度）
    dx = end_x - start_x
    dy = end_y - start_y
    distance = math.sqrt(dx**2 + dy**2)
    
    # 控制点偏移量（距离越远，弧度越大）
    offset = min(distance * 0.2, 50)
    
    # 随机决定弧度方向
    direction = random.choice([-1, 1])
    
    # 控制点
    ctrl1_x = start_x + dx * 0.25 + direction * offset * random.uniform(0.5, 1.0)
    ctrl1_y = start_y + dy * 0.25 - direction * offset * random.uniform(0.3, 0.7)
    ctrl2_x = start_x + dx * 0.75 - direction * offset * random.uniform(0.3, 0.7)
    ctrl2_y = start_y + dy * 0.75 + direction * offset * random.uniform(0.5, 1.0)
    
    for i in range(steps + 1):
        # 使用 ease-out 缓动（先快后慢）
        t = i / steps
        t = 1 - (1 - t) ** 2  # ease-out quadratic
        
        x = _bezier_curve(t, start_x, ctrl1_x, ctrl2_x, end_x)
        y = _bezier_curve(t, start_y, ctrl1_y, ctrl2_y, end_y)
        
        # 添加轻微抖动（越接近终点抖动越小）
        jitter = (1 - t) * 2
        x += random.uniform(-jitter, jitter)
        y += random.uniform(-jitter, jitter)
        
        path.append((x, y))
    
    # 确保最后一个点精确到达目标
    path[-1] = (end_x, end_y)
    
    return path


def _get_human_type_delay() -> int:
    """获取人类化的输入延迟（毫秒），保持自然但不过慢"""
    base_delay = random.randint(RPAConfig.type_delay_min, RPAConfig.type_delay_max)
    if random.random() < 0.03:
        base_delay += random.randint(60, 150)
    if random.random() < 0.15:
        base_delay = max(10, base_delay - 20)
    return base_delay


# 平滑滚动 JavaScript
_SMOOTH_SCROLL_JS = """
(targetY, duration) => {
    return new Promise(resolve => {
        const startY = window.scrollY;
        const distance = targetY - startY;
        const startTime = performance.now();
        
        function step(currentTime) {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            
            // ease-in-out cubic
            const ease = progress < 0.5 
                ? 4 * progress * progress * progress 
                : 1 - Math.pow(-2 * progress + 2, 3) / 2;
            
            window.scrollTo(0, startY + distance * ease);
            
            if (progress < 1) {
                requestAnimationFrame(step);
            } else {
                resolve();
            }
        }
        
        requestAnimationFrame(step);
    });
}
"""

logger = logging.getLogger("lingque.browser.playwright")

playwright_async = None


def _ensure_playwright():
    global playwright_async
    if playwright_async is None:
        try:
            from playwright.async_api import async_playwright
            playwright_async = async_playwright
        except ImportError:
            raise ImportError(
                "请安装 playwright: pip install playwright && playwright install chromium"
            )


# ==================== 系统 Chrome 检测 ====================

def _find_chrome_executable() -> Optional[str]:
    """自动检测系统安装的 Chrome/Edge/Brave 浏览器路径"""
    if sys.platform == "win32":
        candidates = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            os.path.expandvars(r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    else:  # Linux
        candidates = []
        for cmd in ("google-chrome", "google-chrome-stable", "chromium-browser",
                    "chromium", "microsoft-edge", "brave-browser"):
            path = shutil.which(cmd)
            if path:
                candidates.append(path)
        candidates.extend([
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/usr/bin/microsoft-edge",
            "/usr/bin/brave-browser",
        ])

    for path in candidates:
        if os.path.isfile(path):
            logger.info(f"检测到系统浏览器: {path}")
            return path

    return None


async def _wait_for_cdp_ready(port: int, timeout: float = 15.0) -> bool:
    """轮询等待 CDP 端口就绪"""
    url = f"http://127.0.0.1:{port}/json/version"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    info = resp.json()
                    logger.info(f"CDP 已就绪: {info.get('Browser', 'unknown')}")
                    return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


@dataclass
class BrowserResult:
    success: bool
    data: str = ""
    screenshot: Optional[bytes] = None
    error: str = ""


# ==================== Stealth 反检测 ====================

_STEALTH_JS = """
(() => {
  // 会话稳定种子：同一浏览器实例内指纹一致，不同实例间有差异
  const _s = (Math.random() * 0xFFFFFF) | 0;
  const _pick = (arr) => arr[_s % arr.length];

  // ===== 1. navigator.webdriver → undefined =====
  const nd = Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver');
  if (nd) {
    Object.defineProperty(Navigator.prototype, 'webdriver', { get: () => undefined, configurable: true });
  } else {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined, configurable: true });
  }

  // ===== 2. navigator.plugins =====
  Object.defineProperty(navigator, 'plugins', {
    get: () => {
      const mk = (name, desc, fn, mt) => {
        const m = { type: mt, suffixes: '', description: desc, enabledPlugin: null };
        const p = { name, description: desc, filename: fn, length: 1, 0: m, item: () => m, namedItem: () => m };
        m.enabledPlugin = p; return p;
      };
      const a = [
        mk('Chrome PDF Plugin', 'Portable Document Format', 'internal-pdf-viewer', 'application/x-google-chrome-pdf'),
        mk('Chrome PDF Viewer', '', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', 'application/pdf'),
        mk('Native Client', '', 'internal-nacl-plugin', 'application/x-nacl'),
      ];
      a.length = 3; return a;
    },
  });

  // ===== 3. navigator.languages =====
  Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh', 'en-US', 'en'] });

  // ===== 4. 硬件参数随机化 =====
  const cpuCores = _pick([4, 6, 8, 12, 16]);
  const devMem = _pick([4, 8, 8, 16]);
  Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => cpuCores });
  Object.defineProperty(navigator, 'deviceMemory', { get: () => devMem, configurable: true });

  // ===== 5. window.chrome =====
  if (!window.chrome) window.chrome = {};
  if (!window.chrome.runtime) {
    window.chrome.runtime = {
      connect: () => {}, sendMessage: () => {},
      onMessage: { addListener: () => {}, removeListener: () => {} }, id: undefined,
    };
  }
  window.chrome.csi = window.chrome.csi || (() => ({}));
  window.chrome.loadTimes = window.chrome.loadTimes || (() => ({
    requestTime: Date.now()/1000, startLoadTime: Date.now()/1000,
    firstPaintAfterLoadTime: 0, firstPaintTime: Date.now()/1000,
    commitLoadTime: Date.now()/1000, finishDocumentLoadTime: Date.now()/1000,
    finishLoadTime: Date.now()/1000, navigationType: 'Other',
    connectionInfo: 'h2', npnNegotiatedProtocol: 'h2',
    wasAlternateProtocolAvailable: false, wasFetchedViaSpdy: true, wasNpnNegotiated: true,
  }));

  // ===== 6. Permissions API =====
  const origQuery = window.Permissions?.prototype?.query;
  if (origQuery) {
    window.Permissions.prototype.query = function(desc) {
      if (desc?.name === 'notifications') return Promise.resolve({ state: Notification.permission });
      return origQuery.call(this, desc);
    };
  }

  // ===== 7. WebGL 渲染器随机化 =====
  const gpus = [
    ['Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 6GB Direct3D11 vs_5_0 ps_5_0, D3D11)'],
    ['Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)'],
    ['Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0, D3D11)'],
    ['Google Inc. (NVIDIA)', 'ANGLE (NVIDIA, NVIDIA GeForce RTX 2060 Direct3D11 vs_5_0 ps_5_0, D3D11)'],
    ['Google Inc. (AMD)', 'ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)'],
    ['Google Inc. (Intel)', 'ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)'],
  ];
  const gpu = _pick(gpus);
  const _patchWebGL = (proto) => {
    if (!proto?.getParameter) return;
    const orig = proto.getParameter;
    proto.getParameter = new Proxy(orig, {
      apply(target, self, args) {
        const ext = self.getExtension('WEBGL_debug_renderer_info');
        if (ext) {
          if (args[0] === ext.UNMASKED_VENDOR_WEBGL) return gpu[0];
          if (args[0] === ext.UNMASKED_RENDERER_WEBGL) return gpu[1];
        }
        return target.apply(self, args);
      }
    });
  };
  _patchWebGL(WebGLRenderingContext?.prototype);
  _patchWebGL(WebGL2RenderingContext?.prototype);

  // ===== 8. 移除自动化全局变量 =====
  delete window.__playwright;
  delete window.__pw_manual;
  delete window.__puppeteer_evaluation_script__;

  // ===== 9. iframe 代理 =====
  try {
    const origCW = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'contentWindow');
    if (origCW?.get) {
      Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function() {
          const w = origCW.get.call(this);
          if (w) { try { Object.defineProperty(w.navigator, 'webdriver', { get: () => undefined }); } catch(e) {} }
          return w;
        }
      });
    }
  } catch(e) {}

  // ===== 10. Canvas 指纹随机化 =====
  const _addCanvasNoise = (canvas) => {
    try {
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      const w = canvas.width, h = canvas.height;
      if (w === 0 || h === 0) return;
      const imageData = ctx.getImageData(0, 0, Math.min(w, 16), Math.min(h, 16));
      const d = imageData.data;
      for (let i = 0; i < d.length; i += 4) {
        d[i] = d[i] ^ (_s >> (i % 8) & 1);
      }
      ctx.putImageData(imageData, 0, 0);
    } catch(e) {}
  };
  const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function() {
    _addCanvasNoise(this);
    return _origToDataURL.apply(this, arguments);
  };
  const _origToBlob = HTMLCanvasElement.prototype.toBlob;
  if (_origToBlob) {
    HTMLCanvasElement.prototype.toBlob = function() {
      _addCanvasNoise(this);
      return _origToBlob.apply(this, arguments);
    };
  }
  const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
  CanvasRenderingContext2D.prototype.getImageData = function() {
    const imageData = _origGetImageData.apply(this, arguments);
    const d = imageData.data;
    for (let i = 0; i < Math.min(d.length, 64); i += 4) {
      d[i] = d[i] ^ (_s >> (i % 8) & 1);
    }
    return imageData;
  };

  // ===== 11. WebRTC IP 泄漏防护 =====
  if (window.RTCPeerConnection) {
    const OrigRTC = window.RTCPeerConnection;
    window.RTCPeerConnection = function(config, constraints) {
      if (config && config.iceServers) {
        config.iceServers = config.iceServers.filter(s => {
          const urls = Array.isArray(s.urls) ? s.urls : [s.urls || s.url || ''];
          return !urls.some(u => typeof u === 'string' && u.startsWith('stun:'));
        });
      }
      return new OrigRTC(config, constraints);
    };
    window.RTCPeerConnection.prototype = OrigRTC.prototype;
    Object.defineProperty(window, 'RTCPeerConnection', { writable: false, configurable: false });
  }
  if (window.webkitRTCPeerConnection) {
    window.webkitRTCPeerConnection = window.RTCPeerConnection;
  }

  // ===== 12. AudioContext 指纹随机化 =====
  const _patchAudio = (ACtx) => {
    if (!ACtx?.prototype) return;
    const origCreate = ACtx.prototype.createAnalyser;
    if (origCreate) {
      ACtx.prototype.createAnalyser = function() {
        const analyser = origCreate.apply(this, arguments);
        const origGetFloat = analyser.getFloatFrequencyData.bind(analyser);
        analyser.getFloatFrequencyData = function(array) {
          origGetFloat(array);
          for (let i = 0; i < Math.min(array.length, 32); i++) {
            array[i] += (_s % 7 - 3) * 0.001;
          }
        };
        return analyser;
      };
    }
    const origGetChannelData = AudioBuffer?.prototype?.getChannelData;
    if (origGetChannelData) {
      AudioBuffer.prototype.getChannelData = function(channel) {
        const data = origGetChannelData.call(this, channel);
        if (data.length > 0) {
          for (let i = 0; i < Math.min(data.length, 10); i++) {
            data[i] += (_s % 5 - 2) * 1e-7;
          }
        }
        return data;
      };
    }
  };
  _patchAudio(window.AudioContext);
  _patchAudio(window.webkitAudioContext);

  // ===== 13. ClientRects 指纹随机化 =====
  const _rectNoise = (_s % 7 + 1) * 0.00001;
  const _origGetBCR = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function() {
    const rect = _origGetBCR.call(this);
    return new DOMRect(
      rect.x + _rectNoise, rect.y + _rectNoise,
      rect.width + _rectNoise, rect.height + _rectNoise
    );
  };
  const _origGetCR = Element.prototype.getClientRects;
  Element.prototype.getClientRects = function() {
    const rects = _origGetCR.call(this);
    const out = [];
    for (let i = 0; i < rects.length; i++) {
      const r = rects[i];
      out.push(new DOMRect(r.x + _rectNoise, r.y + _rectNoise, r.width + _rectNoise, r.height + _rectNoise));
    }
    out.item = (idx) => out[idx];
    Object.defineProperty(out, 'length', { value: rects.length });
    return out;
  };
})();
"""


# ==================== 字体 CDN 拦截（防止外部字体加载超时导致截图失败） ====================

_BLOCKED_FONT_DOMAINS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "use.typekit.net",
    "fast.fonts.net",
    "cloud.typography.com",
    "use.fontawesome.com",
    "cdn.jsdelivr.net/npm/@fontsource",
    "at.alicdn.com",
    "cdn.bootcdn.net",
)


async def _block_font_route(route):
    """拦截外部字体请求，返回空响应避免 30 秒超时"""
    url = route.request.url
    if route.request.resource_type == "font" or any(d in url for d in _BLOCKED_FONT_DOMAINS):
        await route.fulfill(status=200, content_type="font/woff2", body=b"")
    else:
        await route.continue_()


async def _setup_font_blocking(context):
    """为浏览器上下文注册字体拦截路由"""
    for domain in _BLOCKED_FONT_DOMAINS:
        await context.route(f"**/{domain}/**", _block_font_route)
    await context.route("**/*.woff2", _block_font_route)
    await context.route("**/*.woff", _block_font_route)


# ==================== Cookie 管理 ====================

COOKIES_DIR = Path("workspaces/cookies")


def _load_cookies_for_domain(domain: str) -> list[dict]:
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    all_cookies = []
    for cookie_file in COOKIES_DIR.glob("*.json"):
        cookie_domain = cookie_file.stem
        if cookie_domain in domain or domain.endswith("." + cookie_domain):
            try:
                cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
                all_cookies.extend(cookies)
            except Exception as e:
                logger.warning(f"加载 Cookie 失败 ({cookie_domain}): {e}")
    return all_cookies


def _save_cookies_for_domain(domain: str, cookies: list[dict]) -> int:
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    filtered = [c for c in cookies if domain in c.get("domain", "")]
    if not filtered:
        filtered = cookies
    cookie_file = COOKIES_DIR / f"{domain}.json"
    cookie_file.write_text(json.dumps(filtered, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(filtered)


# ==================== 核心浏览器类 ====================

class PlaywrightBrowser:
    def __init__(
        self,
        headless: bool = True,
        viewport: tuple[int, int] = (1280, 720),
        timeout: int = 30000,
        downloads_dir: str | Path = "./downloads",
        browser_mode: str = "auto",
        cdp_port: int = 9222,
        executable_path: str = "",
    ):
        self.headless = headless
        self.viewport = {"width": viewport[0], "height": viewport[1]}
        self.timeout = timeout
        self.downloads_dir = Path(downloads_dir)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.browser_mode = browser_mode  # "auto" | "cdp" | "builtin"
        self.cdp_port = cdp_port
        self.executable_path = executable_path

        self._pw_context_manager = None
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._chrome_process: Optional[subprocess.Popen] = None
        self._user_data_dir: Optional[str] = None
        self._using_cdp = False

    async def start(self):
        _ensure_playwright()
        self._pw_context_manager = playwright_async()
        self._playwright = await self._pw_context_manager.__aenter__()

        use_cdp = False
        if self.browser_mode in ("cdp", "auto"):
            chrome_path = self.executable_path or _find_chrome_executable()
            if chrome_path:
                use_cdp = True
            elif self.browser_mode == "cdp":
                raise RuntimeError(
                    "BROWSER_MODE=cdp 但未找到系统 Chrome/Edge/Brave，"
                    "请安装浏览器或设置 BROWSER_EXECUTABLE_PATH"
                )

        if use_cdp:
            await self._start_cdp(chrome_path)
        else:
            await self._start_builtin()

    @staticmethod
    def _get_persistent_profile_dir() -> str:
        """获取持久化 Chrome 用户目录（保留 Cookie/LocalStorage/指纹一致性）"""
        base = os.environ.get("WORKSPACE_DIR", "workspaces")
        profile_dir = os.path.join(base, ".chrome_profile")
        os.makedirs(profile_dir, exist_ok=True)
        return profile_dir

    async def _start_cdp(self, chrome_path: str):
        """启动系统 Chrome 并通过 CDP 连接（真实浏览器指纹，绕过反爬）"""
        self._user_data_dir = self._get_persistent_profile_dir()
        self._owns_user_data_dir = False

        stderr_dir = tempfile.mkdtemp(prefix="lingque_cdp_log_")
        stderr_path = os.path.join(stderr_dir, "chrome_stderr.log")

        args = [
            chrome_path,
            f"--remote-debugging-port={self.cdp_port}",
            f"--user-data-dir={self._user_data_dir}",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-gpu",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-sync",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-features=Translate,MediaRouter,BackForwardCache",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--lang=zh-CN",
            f"--window-size={self.viewport['width']},{self.viewport['height']}",
        ]
        if self.headless:
            args.append("--headless=new")
        args.append("about:blank")

        stderr_file = open(stderr_path, "w")
        self._chrome_process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
        )

        if not await _wait_for_cdp_ready(self.cdp_port, timeout=20):
            self._chrome_process.terminate()
            chrome_err = ""
            try:
                stderr_file.close()
                chrome_err = open(stderr_path).read(2000)
            except Exception:
                pass
            try:
                shutil.rmtree(stderr_dir, ignore_errors=True)
            except Exception:
                pass
            raise RuntimeError(
                f"Chrome CDP 启动超时 (port={self.cdp_port})"
                + (f"\nChrome 错误: {chrome_err}" if chrome_err else "")
            )
        try:
            stderr_file.close()
            shutil.rmtree(stderr_dir, ignore_errors=True)
        except Exception:
            pass

        cdp_url = f"http://127.0.0.1:{self.cdp_port}"
        self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)

        if self._browser.contexts:
            self._context = self._browser.contexts[0]
        else:
            self._context = await self._browser.new_context(
                viewport=self.viewport,
                accept_downloads=True,
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )

        await self._context.add_init_script(_STEALTH_JS)
        await _setup_font_blocking(self._context)

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        try:
            await self._page.evaluate(_STEALTH_JS)
        except Exception:
            pass

        self._page.set_default_timeout(self.timeout)
        self._using_cdp = True
        logger.info(f"CDP 模式启动成功 (stealth): {chrome_path}")

    async def _start_builtin(self):
        """回退：使用 Playwright 内置 Chromium（兼容旧行为）"""
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                f"--window-size={self.viewport['width']},{self.viewport['height']}",
            ],
        )
        self._context = await self._browser.new_context(
            viewport=self.viewport,
            accept_downloads=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )

        await self._context.add_init_script(_STEALTH_JS)
        await _setup_font_blocking(self._context)

        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.timeout)
        self._using_cdp = False
        self._owns_user_data_dir = False
        logger.info("内置 Chromium 模式启动（已注入 stealth 反检测）")

    async def stop(self):
        try:
            if self._browser:
                await self._browser.close()
        except Exception as e:
            logger.warning(f"关闭浏览器连接异常: {e}")

        if self._chrome_process:
            try:
                self._chrome_process.terminate()
                self._chrome_process.wait(timeout=5)
            except Exception as e:
                logger.warning(f"终止 Chrome 进程异常: {e}")
                try:
                    self._chrome_process.kill()
                except Exception:
                    pass
            self._chrome_process = None

        try:
            if self._pw_context_manager:
                await self._pw_context_manager.__aexit__(None, None, None)
        except Exception as e:
            logger.warning(f"关闭 Playwright 异常: {e}")

        if self._user_data_dir and getattr(self, '_owns_user_data_dir', True):
            try:
                shutil.rmtree(self._user_data_dir, ignore_errors=True)
            except Exception:
                pass
        self._user_data_dir = None

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._pw_context_manager = None
        self._using_cdp = False
        logger.info("浏览器已关闭")

    async def load_cookies_for_url(self, url: str) -> int:
        if not self._context:
            return 0
        domain = urlparse(url).netloc
        cookies = _load_cookies_for_domain(domain)
        if cookies:
            await self._context.add_cookies(cookies)
            logger.info(f"已加载 {len(cookies)} 个 Cookie ({domain})")
        return len(cookies)

    async def save_current_cookies(self) -> int:
        if not self._context or not self._page:
            return 0
        domain = urlparse(self._page.url).netloc
        cookies = await self._context.cookies()
        if cookies:
            count = _save_cookies_for_domain(domain, cookies)
            logger.info(f"已保存 {count} 个 Cookie ({domain})")
            return count
        return 0

    async def wait_for_page_ready(self, timeout: float = 15.0) -> str:
        """
        智能等待页面渲染完成。多维度检测，避免截到空白页。
        优化版：减少轮询次数和网络空闲等待，提升操作丝滑度。
        """
        if not self._page:
            return "浏览器未启动"

        start = time.time()

        # 阶段 1: 等 DOM 加载（上限 5s）
        try:
            await self._page.wait_for_load_state("domcontentloaded", timeout=min(timeout * 1000, 5000))
        except Exception:
            pass

        # 阶段 2: 尝试等网络空闲（短超时，SPA 经常不会 idle，最多 2s）
        try:
            await self._page.wait_for_load_state("networkidle", timeout=min(timeout * 1000 * 0.2, 2000))
        except Exception:
            pass

        # 阶段 3: 快速内容检测
        check_js = """
        () => {
            const body = document.body;
            if (!body) return { ready: false, reason: 'no_body' };
            const textLen = (body.innerText || '').trim().length;
            const images = document.images;
            const totalImg = images.length;
            let loadedImg = 0;
            for (const img of images) {
                if (img.complete && img.naturalWidth > 0) loadedImg++;
            }
            const hasContent = textLen > 50;
            const imgProgress = totalImg === 0 ? 1.0 : loadedImg / totalImg;
            return {
                ready: hasContent && imgProgress >= 0.5,
                textLen, totalImg, loadedImg,
                reason: !hasContent ? 'no_content' : imgProgress < 0.5 ? 'images_loading' : 'ok'
            };
        }
        """

        max_checks = 5
        interval = 0.3
        last_state = None

        for i in range(max_checks):
            elapsed = time.time() - start
            if elapsed >= timeout:
                break
            try:
                state = await self._page.evaluate(check_js)
            except Exception:
                await asyncio.sleep(interval)
                continue

            if state.get("ready"):
                logger.debug(f"页面就绪: {state['textLen']}字, 图片{state['loadedImg']}/{state['totalImg']}")
                return "ready"
            last_state = state
            await asyncio.sleep(interval)

        if last_state:
            logger.info(
                f"页面等待超时({timeout:.1f}s): {last_state.get('reason', 'unknown')}, "
                f"文字{last_state.get('textLen', 0)}字, "
                f"图片{last_state.get('loadedImg', 0)}/{last_state.get('totalImg', 0)}"
            )
        return last_state.get("reason", "timeout") if last_state else "timeout"

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self.load_cookies_for_url(url)
            await self._page.goto(url, wait_until=wait_until)
            try:
                await self._page.evaluate(_STEALTH_JS)
            except Exception:
                pass
            await self.wait_for_page_ready(timeout=12)
            title = await self._page.title()
            return BrowserResult(success=True, data=f"已导航到: {url}\n页面标题: {title}")
        except Exception as e:
            return BrowserResult(success=False, error=str(e))

    async def go_back(self) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.go_back(wait_until="domcontentloaded")
            await self.wait_for_page_ready(timeout=8)
            title = await self._page.title()
            return BrowserResult(success=True, data=f"已后退\n页面: {title}\nURL: {self._page.url}")
        except Exception as e:
            return BrowserResult(success=False, error=f"后退失败: {e}")

    async def go_forward(self) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.go_forward(wait_until="domcontentloaded")
            await self.wait_for_page_ready(timeout=8)
            title = await self._page.title()
            return BrowserResult(success=True, data=f"已前进\n页面: {title}\nURL: {self._page.url}")
        except Exception as e:
            return BrowserResult(success=False, error=f"前进失败: {e}")

    async def click(self, selector: str) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.click(selector)
            return BrowserResult(success=True, data=f"已点击: {selector}")
        except Exception as e:
            return BrowserResult(success=False, error=f"点击失败: {e}")

    async def fill(self, selector: str, text: str) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.fill(selector, text)
            return BrowserResult(success=True, data=f"已填写 {selector}")
        except Exception as e:
            return BrowserResult(success=False, error=f"填写失败: {e}")

    async def type_text(self, selector: str, text: str, delay: int = 50) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.type(selector, text, delay=delay)
            return BrowserResult(success=True, data=f"已输入: {text[:50]}...")
        except Exception as e:
            return BrowserResult(success=False, error=f"输入失败: {e}")

    async def scroll(self, direction: str = "down", amount: int = 500) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            js_map = {
                "down": f"window.scrollBy(0, {amount})",
                "up": f"window.scrollBy(0, -{amount})",
                "top": "window.scrollTo(0, 0)",
                "bottom": "window.scrollTo(0, document.body.scrollHeight)",
            }
            await self._page.evaluate(js_map.get(direction, js_map["down"]))
            return BrowserResult(success=True, data=f"已滚动: {direction} {amount}px")
        except Exception as e:
            return BrowserResult(success=False, error=f"滚动失败: {e}")

    # ==================== RPA 风格操作方法（反爬绕过）====================

    async def rpa_move_mouse_to(self, locator):
        """平滑移动鼠标到元素位置（贝塞尔曲线轨迹）"""
        if not RPAConfig.enabled:
            return
        
        try:
            # 获取元素中心位置
            box = await locator.bounding_box()
            if not box:
                return
            
            target_x = box["x"] + box["width"] / 2
            target_y = box["y"] + box["height"] / 2
            
            # 获取当前鼠标位置（如果没有，从随机位置开始）
            viewport = self._page.viewport_size
            if viewport:
                start_x = random.uniform(viewport["width"] * 0.3, viewport["width"] * 0.7)
                start_y = random.uniform(viewport["height"] * 0.3, viewport["height"] * 0.7)
            else:
                start_x, start_y = 400, 300
            
            # 生成贝塞尔曲线路径
            path = _generate_mouse_path(start_x, start_y, target_x, target_y, RPAConfig.mouse_steps)
            
            # 按路径移动鼠标
            step_delay = RPAConfig.mouse_move_duration / len(path)
            for x, y in path:
                await self._page.mouse.move(x, y)
                await asyncio.sleep(step_delay)
            
            # 到达目标后短暂停顿
            await asyncio.sleep(RPAConfig.click_delay)
            
        except Exception as e:
            logger.debug(f"鼠标移动失败: {e}")

    async def rpa_click(self, locator) -> BrowserResult:
        """RPA 风格点击（平滑移动鼠标 + 点击，绕过反爬）"""
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        
        try:
            if RPAConfig.enabled:
                # 平滑移动鼠标到目标位置
                await self.rpa_move_mouse_to(locator)
                await locator.click()
            else:
                await locator.click()
            
            return BrowserResult(success=True, data="点击成功")
        except Exception as e:
            return BrowserResult(success=False, error=f"点击失败: {e}")

    async def rpa_type(self, locator, text: str, clear: bool = True) -> BrowserResult:
        """RPA 风格输入（平滑移动 + 人类化逐字输入，绕过反爬）"""
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        
        try:
            if RPAConfig.enabled:
                # 1. 平滑移动鼠标并点击聚焦
                await self.rpa_move_mouse_to(locator)
                await locator.click()
                await asyncio.sleep(0.05)
                
                # 2. 清空（如果需要）
                if clear:
                    await locator.fill("")
                
                # 3. 人类化逐字输入（随机延迟）
                for char in text:
                    await locator.type(char, delay=0)
                    delay = _get_human_type_delay()
                    await asyncio.sleep(delay / 1000)
            else:
                if clear:
                    await locator.fill("")
                await locator.type(text, delay=50)
            
            return BrowserResult(success=True, data=f"已输入: {text[:30]}...")
        except Exception as e:
            return BrowserResult(success=False, error=f"输入失败: {e}")

    async def rpa_scroll(self, direction: str = "down", amount: int = 500) -> BrowserResult:
        """RPA 风格平滑滚动"""
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        
        try:
            if RPAConfig.enabled and RPAConfig.scroll_smooth:
                current_scroll = await self._page.evaluate("window.scrollY")
                
                if direction == "down":
                    target_scroll = current_scroll + amount
                elif direction == "up":
                    target_scroll = max(0, current_scroll - amount)
                elif direction == "top":
                    target_scroll = 0
                elif direction == "bottom":
                    target_scroll = await self._page.evaluate("document.body.scrollHeight")
                else:
                    target_scroll = current_scroll + amount
                
                # 使用平滑滚动 JS
                duration = 300  # 毫秒
                await self._page.evaluate(f"({_SMOOTH_SCROLL_JS})({target_scroll}, {duration})")
                await asyncio.sleep(duration / 1000 + 0.1)
                
                return BrowserResult(success=True, data=f"已平滑滚动: {direction} {amount}px")
            else:
                return await self.scroll(direction, amount)
        except Exception as e:
            return BrowserResult(success=False, error=f"滚动失败: {e}")

    async def screenshot(self, full_page: bool = False) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            screenshot_bytes = await self._page.screenshot(full_page=full_page)
            return BrowserResult(success=True, data=f"截图成功 ({len(screenshot_bytes)} bytes)", screenshot=screenshot_bytes)
        except Exception as e:
            return BrowserResult(success=False, error=f"截图失败: {e}")

    async def save_screenshot(self, path: str | Path, full_page: bool = False) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.screenshot(path=str(path), full_page=full_page)
            return BrowserResult(success=True, data=f"截图已保存: {path}")
        except Exception as e:
            return BrowserResult(success=False, error=f"保存截图失败: {e}")

    async def pdf(self, path: str | Path) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.pdf(path=str(path))
            return BrowserResult(success=True, data=f"PDF 已保存: {path}")
        except Exception as e:
            return BrowserResult(success=False, error=f"导出 PDF 失败: {e}")

    async def get_text(self, selector: str = "body") -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            text = await self._page.inner_text(selector)
            return BrowserResult(success=True, data=text[:5000])
        except Exception as e:
            return BrowserResult(success=False, error=f"获取文本失败: {e}")

    async def get_html(self, selector: str = "body") -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            html = await self._page.inner_html(selector)
            return BrowserResult(success=True, data=html[:10000])
        except Exception as e:
            return BrowserResult(success=False, error=f"获取 HTML 失败: {e}")

    async def evaluate(self, js_code: str) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            result = await self._page.evaluate(js_code)
            return BrowserResult(success=True, data=str(result))
        except Exception as e:
            return BrowserResult(success=False, error=f"JS 执行失败: {e}")

    async def wait_for_selector(self, selector: str, timeout: int = None) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.wait_for_selector(selector, timeout=timeout or self.timeout)
            return BrowserResult(success=True, data=f"元素已出现: {selector}")
        except Exception as e:
            return BrowserResult(success=False, error=f"等待超时: {e}")

    async def download(self, trigger_selector: str) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            async with self._page.expect_download() as download_info:
                await self._page.click(trigger_selector)
            download = await download_info.value
            save_path = self.downloads_dir / download.suggested_filename
            await download.save_as(str(save_path))
            return BrowserResult(success=True, data=f"下载完成: {save_path}")
        except Exception as e:
            return BrowserResult(success=False, error=f"下载失败: {e}")

    async def select_option(self, selector: str, value: str) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.select_option(selector, value)
            return BrowserResult(success=True, data=f"已选择: {value}")
        except Exception as e:
            return BrowserResult(success=False, error=f"选择失败: {e}")

    async def check(self, selector: str) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.check(selector)
            return BrowserResult(success=True, data=f"已勾选: {selector}")
        except Exception as e:
            return BrowserResult(success=False, error=f"勾选失败: {e}")

    async def upload_file(self, selector: str, file_path: str | Path) -> BrowserResult:
        if not self._page:
            return BrowserResult(success=False, error="浏览器未启动")
        try:
            await self._page.set_input_files(selector, str(file_path))
            return BrowserResult(success=True, data=f"已上传: {file_path}")
        except Exception as e:
            return BrowserResult(success=False, error=f"上传失败: {e}")

    def get_current_url(self) -> str:
        return self._page.url if self._page else ""

    # ==================== 标签页管理 ====================

    async def list_tabs(self) -> list[dict]:
        if not self._context:
            return []
        tabs = []
        for i, page in enumerate(self._context.pages):
            try:
                title = await page.title()
            except Exception:
                title = "(无法获取)"
            tabs.append({
                "index": i,
                "title": title,
                "url": page.url,
                "active": page == self._page,
            })
        return tabs

    async def switch_tab(self, index: int) -> BrowserResult:
        if not self._context:
            return BrowserResult(success=False, error="浏览器未启动")
        pages = self._context.pages
        if index < 0 or index >= len(pages):
            return BrowserResult(success=False, error=f"标签页索引超范围: {index} (共 {len(pages)} 个)")
        self._page = pages[index]
        await self._page.bring_to_front()
        title = await self._page.title()
        return BrowserResult(success=True, data=f"已切换到标签页 {index}: {title}\nURL: {self._page.url}")

    async def new_tab(self, url: str = "") -> BrowserResult:
        if not self._context:
            return BrowserResult(success=False, error="浏览器未启动")
        self._page = await self._context.new_page()
        self._page.set_default_timeout(self.timeout)
        if url:
            await self.load_cookies_for_url(url)
            await self._page.goto(url, wait_until="domcontentloaded")
        title = await self._page.title()
        return BrowserResult(success=True, data=f"新标签页已打开: {title}\nURL: {self._page.url}")

    async def close_tab(self, index: int = -1) -> BrowserResult:
        if not self._context:
            return BrowserResult(success=False, error="浏览器未启动")
        pages = self._context.pages
        if index == -1:
            target = self._page
        elif 0 <= index < len(pages):
            target = pages[index]
        else:
            return BrowserResult(success=False, error=f"标签页索引超范围: {index}")

        await target.close()

        remaining = self._context.pages
        if remaining:
            self._page = remaining[-1]
            title = await self._page.title()
            return BrowserResult(success=True, data=f"标签页已关闭，当前: {title}")
        else:
            self._page = await self._context.new_page()
            return BrowserResult(success=True, data="标签页已关闭，已打开空白页")


class BrowserSession:
    def __init__(self, headless: bool = True, timeout: int = 30000):
        self.headless = headless
        self.timeout = timeout
        self._browser = None

    async def __aenter__(self) -> PlaywrightBrowser:
        self._browser = PlaywrightBrowser(
            headless=self.headless,
            timeout=self.timeout,
            browser_mode=os.environ.get("BROWSER_MODE", "auto"),
            cdp_port=int(os.environ.get("BROWSER_CDP_PORT", "9223")),
            executable_path=os.environ.get("BROWSER_EXECUTABLE_PATH", ""),
        )
        await self._browser.start()
        return self._browser

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            try:
                await self._browser.stop()
            except Exception as e:
                logger.warning(f"浏览器关闭异常: {e}")
            self._browser = None


async def run_browser_task(task_func, *args, **kwargs) -> BrowserResult:
    try:
        async with BrowserSession() as browser:
            return await task_func(browser, *args, **kwargs)
    except Exception as e:
        return BrowserResult(success=False, error=str(e))


# ==================== 增强版无障碍树快照（aria_snapshot + 回退 JS）====================

_INTERACTIVE_ROLES = {
    "link", "button", "textbox", "combobox", "searchbox",
    "radio", "checkbox", "menuitem", "tab", "switch",
    "spinbutton", "slider", "option", "treeitem", "listitem",
}

_CONTENT_ROLES = {
    "heading", "img", "table", "list",
    "navigation", "main", "article", "banner", "dialog",
}

_ROLE_LABELS = {
    "link": "链接", "button": "按钮", "textbox": "输入框",
    "combobox": "下拉框", "searchbox": "搜索框", "radio": "单选",
    "checkbox": "复选框", "menuitem": "菜单项", "tab": "标签页",
    "switch": "开关", "spinbutton": "数字输入", "slider": "滑块",
    "option": "选项", "listitem": "列表项", "treeitem": "树节点",
    "heading": "标题", "img": "图片", "table": "表格",
    "list": "列表", "navigation": "导航", "dialog": "弹窗",
}

_REF_PATTERN = re.compile(r"^e\d+$")
_XPATH_PATTERN = re.compile(r"^(//|/html)")

# aria_snapshot YAML 行解析：匹配 "- role 'name' [attr=val]:" 或 "- role 'name'"
_ARIA_LINE_RE = re.compile(
    r'^(\s*)- '
    r'(\w+)'                   # role
    r'(?:\s+"([^"]*)")?'       # optional "name"
    r'(?:\s+\[([^\]]*)\])?'   # optional [attributes]
    r'\s*:?\s*$'
)


def _parse_aria_snapshot(yaml_text: str, max_elements: int = 50) -> tuple[list[dict], list[dict]]:
    """
    解析 Playwright aria_snapshot() 的 YAML 输出，分离交互元素和结构元素。
    返回 (interactive_elements, structural_elements)
    """
    interactive = []
    structural = []

    for line in yaml_text.splitlines():
        m = _ARIA_LINE_RE.match(line)
        if not m:
            continue

        indent, role, name, attrs_str = m.groups()
        role = role.strip()
        name = (name or "").strip()

        attr_dict = {}
        if attrs_str:
            for part in attrs_str.split():
                if "=" in part:
                    k, v = part.split("=", 1)
                    attr_dict[k.strip()] = v.strip()

        if role in _INTERACTIVE_ROLES and name:
            if len(interactive) < max_elements:
                el = {"role": role, "name": name[:80]}
                if attr_dict.get("checked"):
                    el["checked"] = attr_dict["checked"] == "true"
                if attr_dict.get("disabled"):
                    el["disabled"] = attr_dict["disabled"] == "true"
                if attr_dict.get("expanded"):
                    el["expanded"] = attr_dict["expanded"] == "true"
                if attr_dict.get("selected"):
                    el["selected"] = attr_dict["selected"] == "true"
                if attr_dict.get("pressed"):
                    el["pressed"] = attr_dict["pressed"] == "true"
                if attr_dict.get("level"):
                    el["level"] = attr_dict["level"]
                interactive.append(el)
        elif role in _CONTENT_ROLES and name:
            if len(structural) < 15:
                el = {"role": role, "name": name[:60]}
                if attr_dict.get("level"):
                    el["level"] = attr_dict["level"]
                structural.append(el)

    return interactive, structural


# JS 回退扫描（当 aria_snapshot 不可用时）
_JS_FALLBACK_ELEMENTS = """
() => {
    const MAX = 150;
    const results = [];
    const seen = new Set();

    const SELECTOR = 'a[href], button, input:not([type="hidden"]), select, textarea, ' +
        '[role="button"], [role="link"], [role="tab"], [role="menuitem"], ' +
        '[role="checkbox"], [role="radio"], [role="switch"], [role="option"], ' +
        '[contenteditable="true"], details > summary, ' +
        '[onclick], [tabindex]:not([tabindex="-1"]), ' +
        '[data-spm], [data-click], [data-e2e], [data-testid], [data-sku]';

    const dialogSelectors = [
        'dialog[open]', '[role="dialog"]', '[role="alertdialog"]',
        '.modal.show', '.modal.active', '.modal[style*="display: block"]',
        '.ant-modal-wrap:not([style*="display: none"])',
        '.el-dialog__wrapper:not([style*="display: none"])',
        '[class*="login-dialog"]', '[class*="loginDialog"]',
        '[class*="login-modal"]', '[class*="loginModal"]',
        '[class*="login-form"]', '[class*="loginForm"]',
        '[class*="SignFlow"]', '[class*="sign-flow"]',
        '.next-overlay-wrapper .next-dialog',
        '[class*="baxia"]', '[id*="baxia"]',
        '.fm-login', '.login-box', '.login-panel', '.login-container',
        '[id*="login-full-panel"]', '[id*="login-panel"]',
        '[class*="login-full-panel"]', '[class*="loginPanel"]',
        '[class*="passport-sdk"]', '[id*="passport-sdk"]',
        '[class*="dy-account"]', '[id*="dy-account"]',
    ];

    let dialogRoot = null;
    for (const sel of dialogSelectors) {
        try {
            const d = document.querySelector(sel);
            if (d) {
                const rect = d.getBoundingClientRect();
                const style = window.getComputedStyle(d);
                if (rect.width > 50 && rect.height > 50 &&
                    style.display !== 'none' && style.visibility !== 'hidden') {
                    dialogRoot = d;
                    break;
                }
            }
        } catch(e) {}
    }

    function processElement(el, inDialog) {
        if (results.length >= MAX) return;
        const rect = el.getBoundingClientRect();
        if (rect.width < 2 || rect.height < 2) return;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return;
        const tag = el.tagName.toLowerCase();
        if (tag === 'html' || tag === 'body' || tag === 'head') return;
        const text = (el.innerText || el.textContent || '').trim().slice(0, 80).replace(/\\n/g, ' ');
        const placeholder = el.placeholder || '';
        const ariaLabel = el.getAttribute('aria-label') || '';
        const type = el.type || '';
        const value = el.value || '';
        const id = el.id;
        const elName = el.name;
        const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
        const checked = el.checked;
        const required = el.required;

        let role, label;
        if (tag === 'a') { role = 'link'; label = text || ariaLabel || (el.getAttribute('href') || '').slice(0, 40); }
        else if (tag === 'button' || el.getAttribute('role') === 'button' || tag === 'summary') {
            role = 'button'; label = text || ariaLabel || value;
        }
        else if (tag === 'input' && (type === 'submit' || type === 'button')) { role = 'button'; label = value || text || ariaLabel; }
        else if (tag === 'input' && type === 'checkbox') { role = 'checkbox'; label = ariaLabel || text || elName; }
        else if (tag === 'input' && type === 'radio') { role = 'radio'; label = ariaLabel || text || elName; }
        else if (tag === 'input') {
            role = 'textbox';
            label = placeholder || ariaLabel || elName || type;
            if (!label && type === 'tel') label = '手机号';
            if (!label && type === 'password') label = '密码';
            if (!label && type === 'text') label = '文本输入';
        }
        else if (tag === 'textarea') { role = 'textbox'; label = placeholder || ariaLabel || elName; }
        else if (tag === 'select') { role = 'combobox'; label = ariaLabel || elName; }
        else if (el.getAttribute('contenteditable') === 'true') { role = 'textbox'; label = ariaLabel || text.slice(0, 30) || 'editor'; }
        else {
            role = el.getAttribute('role') || '';
            label = text || ariaLabel;
            if (!role) {
                const hasClick = el.hasAttribute('onclick') || el.hasAttribute('data-click') || el.hasAttribute('data-spm');
                const cursorPointer = style.cursor === 'pointer';
                if (hasClick || cursorPointer) {
                    role = 'button';
                    if (!label) {
                        const img = el.querySelector('img');
                        if (img) label = img.alt || img.title || '(图片按钮)';
                    }
                } else if (el.hasAttribute('tabindex')) {
                    role = 'button';
                } else {
                    return;
                }
            }
        }
        if (!label) return;

        let sel;
        if (id) sel = '#' + CSS.escape(id);
        else if (el.getAttribute('data-sku')) sel = '[data-sku="' + el.getAttribute('data-sku') + '"]';
        else if (el.getAttribute('data-e2e')) sel = '[data-e2e="' + el.getAttribute('data-e2e') + '"]';
        else if (el.getAttribute('data-testid')) sel = '[data-testid="' + el.getAttribute('data-testid') + '"]';
        else if (elName && tag !== 'a') sel = tag + '[name="' + elName + '"]';
        else if (placeholder) sel = tag + '[placeholder="' + placeholder + '"]';
        else if (ariaLabel) sel = tag + '[aria-label="' + ariaLabel + '"]';
        else if (type && tag === 'input') sel = 'input[type="' + type + '"]';
        else sel = '';

        const key = role + '|' + label;
        if (seen.has(key)) return;
        seen.add(key);
        results.push({
            role, name: label.slice(0, 80), value: value.slice(0, 30), css: sel,
            disabled: disabled || false, checked: checked || false, required: required || false,
            in_dialog: inDialog || false,
        });
    }

    function scanTree(root, inDialog) {
        try {
            const els = root.querySelectorAll(SELECTOR);
            for (const el of els) { processElement(el, inDialog); }
        } catch(e) {}
        try {
            const allEls = root.querySelectorAll('*');
            for (const el of allEls) {
                if (el.shadowRoot) { scanTree(el.shadowRoot, inDialog); }
            }
        } catch(e) {}
    }

    if (dialogRoot) { scanTree(dialogRoot, true); }

    // 扫描展开的下拉面板
    const dropdownSelectors = [
        '.ant-select-dropdown:not([style*="display: none"])',
        '.el-select-dropdown:not([style*="display: none"])',
        '.el-dropdown-menu', '[role="listbox"]', '[role="menu"]',
        '.dropdown-menu.show', '.dropdown-menu[style*="display: block"]',
        '[class*="select-dropdown"]:not([style*="display: none"])',
        '[class*="SelectDropdown"]:not([style*="display: none"])',
        '[class*="Popup"]:not([style*="display: none"])',
    ];
    for (const sel of dropdownSelectors) {
        try {
            const panels = document.querySelectorAll(sel);
            for (const panel of panels) {
                const rect = panel.getBoundingClientRect();
                if (rect.width < 10 || rect.height < 10) continue;
                const items = panel.querySelectorAll('[role="option"], li, .ant-select-item, .el-select-dropdown__item, [class*="option"]');
                for (const item of items) {
                    if (results.length >= MAX) break;
                    const text = (item.innerText || item.textContent || '').trim().slice(0, 60);
                    if (!text) continue;
                    const key = 'option|' + text;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    results.push({
                        role: 'option', name: text, value: '', css: '',
                        disabled: item.getAttribute('aria-disabled') === 'true',
                        checked: item.getAttribute('aria-selected') === 'true',
                        required: false, in_dialog: false,
                    });
                }
            }
        } catch(e) {}
    }

    scanTree(document, false);

    // 兜底：扫描 cursor:pointer 的可见 div/span（电商平台大量使用）
    if (results.length < 30) {
        const candidates = document.querySelectorAll('div, span, li, img');
        for (const el of candidates) {
            if (results.length >= MAX) break;
            try {
                const st = window.getComputedStyle(el);
                if (st.cursor !== 'pointer') continue;
                const r = el.getBoundingClientRect();
                if (r.width < 20 || r.height < 15 || r.bottom < 0 || r.top > window.innerHeight) continue;
                if (st.display === 'none' || st.visibility === 'hidden') continue;
                if (el.closest('a, button, input, select, textarea')) continue;
                const txt = (el.innerText || el.textContent || '').trim().slice(0, 60).replace(/\\n/g, ' ');
                const aria = el.getAttribute('aria-label') || '';
                const alt = el.alt || el.title || '';
                const lbl = txt || aria || alt;
                if (!lbl || lbl.length < 2) continue;
                const key = 'button|' + lbl;
                if (seen.has(key)) continue;
                seen.add(key);
                let css = '';
                if (el.id) css = '#' + CSS.escape(el.id);
                else if (el.className && typeof el.className === 'string') {
                    const cls = el.className.trim().split(/\\s+/)[0];
                    if (cls && cls.length < 60) css = '.' + CSS.escape(cls);
                }
                results.push({
                    role: 'button', name: lbl.slice(0, 80), value: '', css: css,
                    disabled: false, checked: false, required: false, in_dialog: false,
                });
            } catch(e) {}
        }
    }

    return results;
}
"""

_JS_PAGE_STRUCTURE = """
() => {
    const info = {};
    info.title = document.title;
    info.url = location.href;
    info.forms = document.forms.length;
    info.images = document.images.length;
    info.links = document.links.length;

    const headings = [];
    document.querySelectorAll('h1, h2, h3').forEach(h => {
        const text = h.innerText.trim().slice(0, 60);
        if (text) headings.push({ level: parseInt(h.tagName[1]), text });
    });
    info.headings = headings.slice(0, 8);

    const tables = [];
    document.querySelectorAll('table').forEach((t, i) => {
        if (i >= 3) return;
        const rows = t.rows.length;
        const cols = t.rows[0] ? t.rows[0].cells.length : 0;
        tables.push({ rows, cols });
    });
    info.tables = tables;

    const dialogSels = [
        'dialog[open]', '[role="dialog"]', '[role="alertdialog"]',
        '.modal.show', '.modal.active',
        '.ant-modal-wrap:not([style*="display: none"])',
        '.el-dialog__wrapper:not([style*="display: none"])',
        '[class*="login-dialog"]', '[class*="loginDialog"]',
        '[class*="login-modal"]', '[class*="login-form"]',
        '[class*="SignFlow"]', '[class*="sign-flow"]',
        '.next-overlay-wrapper .next-dialog',
        '.fm-login', '.login-box', '.login-panel',
        '[id*="login-full-panel"]', '[id*="login-panel"]',
        '[class*="login-full-panel"]', '[class*="loginPanel"]',
        '[class*="passport-sdk"]', '[id*="passport-sdk"]',
        '[class*="dy-account"]', '[id*="dy-account"]',
    ];
    let dialogInfo = null;
    for (const sel of dialogSels) {
        try {
            const d = document.querySelector(sel);
            if (d) {
                const r = d.getBoundingClientRect();
                const s = window.getComputedStyle(d);
                if (r.width > 50 && r.height > 50 && s.display !== 'none' && s.visibility !== 'hidden') {
                    const title = (d.querySelector('[class*="title"], h2, h3, .header') || {}).innerText || '';
                    dialogInfo = { detected: true, title: title.trim().slice(0, 40), width: Math.round(r.width), height: Math.round(r.height) };
                    break;
                }
            }
        } catch(e) {}
    }
    info.has_dialog = !!dialogInfo;
    info.dialog_info = dialogInfo;

    const scroll_height = document.documentElement.scrollHeight;
    const viewport_height = window.innerHeight;
    const scroll_top = window.scrollY;
    info.scroll_position = {
        at_top: scroll_top < 50,
        at_bottom: (scroll_top + viewport_height) >= (scroll_height - 50),
        percent: Math.round((scroll_top / Math.max(1, scroll_height - viewport_height)) * 100),
        total_height: scroll_height,
    };

    return info;
}
"""


async def _scan_iframes(page, max_elements: int = 15) -> list[dict]:
    """扫描 iframe 内元素（淘宝/闲鱼登录框通常在 iframe 里），限时 3s"""
    iframe_elements = []
    try:
        frame_count = len(page.frames)
        if frame_count <= 1:
            return []

        deadline = time.time() + 3.0
        for frame in page.frames[1:]:
            if len(iframe_elements) >= max_elements or time.time() > deadline:
                break
            try:
                frame_url = frame.url
                if not frame_url or frame_url == "about:blank":
                    continue

                elements = await asyncio.wait_for(
                    frame.evaluate(_JS_FALLBACK_ELEMENTS), timeout=2.0
                )
                if elements:
                    frame_domain = urlparse(frame_url).netloc or "iframe"
                    for el in elements:
                        el["_frame_url"] = frame_url
                        el["_frame_name"] = frame_domain
                        iframe_elements.append(el)
                        if len(iframe_elements) >= max_elements:
                            break
            except Exception as e:
                logger.debug(f"扫描 iframe 失败 ({frame.url[:50]}): {e}")
                continue
    except Exception as e:
        logger.debug(f"iframe 遍历失败: {e}")

    return iframe_elements


async def _snapshot_elements(browser: PlaywrightBrowser, max_elements: int = 80) -> tuple[str, dict]:
    """
    增强版页面快照。优先使用 aria_snapshot，不可用时回退 JS 扫描。

    返回: (structured_info_text, ref_map)
    """
    if not browser._page:
        return "浏览器未打开", {}

    # 收集页面结构信息
    try:
        page_info = await browser._page.evaluate(_JS_PAGE_STRUCTURE)
    except Exception:
        page_info = {}

    title = page_info.get("title", "")
    url = page_info.get("url", browser._page.url)

    lines = [f"页面: {title}", f"URL: {url}"]

    overview_parts = []
    if page_info.get("forms"):
        overview_parts.append(f"{page_info['forms']}个表单")
    if page_info.get("has_dialog"):
        dialog_info = page_info.get("dialog_info") or {}
        dialog_title = dialog_info.get("title", "")
        overview_parts.append(f"有弹窗{'「'+dialog_title+'」' if dialog_title else ''}")

    frame_count = len(browser._page.frames)
    if frame_count > 1:
        overview_parts.append(f"{frame_count - 1}个iframe")

    sp = page_info.get("scroll_position", {})
    if sp:
        pos_desc = "顶部" if sp.get("at_top") else ("底部" if sp.get("at_bottom") else f"滚动{sp.get('percent', 0)}%")
        overview_parts.append(f"位置:{pos_desc}")
    if overview_parts:
        lines.append(f"概览: {', '.join(overview_parts)}")

    headings = page_info.get("headings", [])
    if headings:
        heading_texts = [f"{'#' * h['level']} {h['text']}" for h in headings[:6]]
        lines.append(f"页面结构: {' | '.join(heading_texts)}")

    lines.append("")

    # === 主快照：aria_snapshot 优先 ===
    raw_elements: list[dict] = []
    snapshot_source = "aria"

    try:
        aria_yaml = await browser._page.locator("body").aria_snapshot()
        if aria_yaml:
            interactive, structural = _parse_aria_snapshot(aria_yaml, max_elements + 10)
            raw_elements = interactive
            if structural:
                struct_strs = [f"{'#'*int(s.get('level',1))} {s['name']}" if s['role'] == 'heading'
                               else f"[{_ROLE_LABELS.get(s['role'], s['role'])}] {s['name']}"
                               for s in structural[:6]]
                if struct_strs:
                    lines.append(f"页面内容: {' | '.join(struct_strs)}")
    except Exception as e:
        logger.debug(f"aria_snapshot 不可用: {e}")

    if len(raw_elements) < 3:
        snapshot_source = "js"
        try:
            js_elements = await browser._page.evaluate(_JS_FALLBACK_ELEMENTS)
            raw_elements = js_elements or []
        except Exception as e:
            logger.warning(f"JS 元素提取也失败: {e}")

    # 扫描 iframe
    iframe_elements = await _scan_iframes(browser._page, max_elements=20)
    has_iframe_elements = len(iframe_elements) > 0

    if not raw_elements and not iframe_elements:
        lines.append("（未检测到可交互元素，页面可能还在加载或是纯展示页面）")
        return "\n".join(lines), {}

    # 去重 + 分配编号
    seen: dict[tuple[str, str], int] = {}
    elements: list[dict] = []
    for el in raw_elements:
        role = el.get("role", "")
        name = (el.get("name") or "").strip()
        if not name:
            continue
        key = (role, name)
        if key in seen:
            seen[key] += 1
            el["nth"] = seen[key]
        else:
            seen[key] = 0
        elements.append(el)
        if len(elements) >= max_elements:
            break

    ref_map: dict[str, dict] = {}

    dialog_elements = [el for el in elements if el.get("in_dialog")]
    page_elements = [el for el in elements if not el.get("in_dialog")]
    ordered_elements = dialog_elements + page_elements

    if dialog_elements:
        lines.append(f"弹窗内元素 ({len(dialog_elements)} 个，优先操作这些):")
    elif ordered_elements:
        lines.append(f"可交互元素 ({len(ordered_elements)} 个，用 [eN] 编号操作):")

    shown_dialog_header = bool(dialog_elements)
    shown_page_header = False

    for i, el in enumerate(ordered_elements):
        if not el.get("in_dialog") and not shown_page_header and shown_dialog_header:
            shown_page_header = True
            lines.append(f"\n页面其他元素 ({len(page_elements)} 个):")

        ref = f"e{i + 1}"
        role = el.get("role", "")
        name = el.get("name", "")
        label = _ROLE_LABELS.get(role, role)

        parts = [f"  [{ref}] {label} \"{name}\""]

        states = []
        if el.get("value"):
            states.append(f"值=\"{el['value'][:20]}\"")
        if el.get("checked"):
            states.append("✓已选")
        if el.get("disabled"):
            states.append("禁用")
        if el.get("expanded") is not None:
            states.append("展开" if el["expanded"] else "收起")
        if el.get("required"):
            states.append("必填")
        if states:
            parts.append(f"  ({', '.join(states)})")

        lines.append("".join(parts))

        ref_info: dict = {"role": role, "name": name, "nth": el.get("nth")}
        if snapshot_source == "js" and el.get("css"):
            ref_info["css"] = el["css"]
        ref_map[ref] = ref_info

    if len(raw_elements) > max_elements:
        lines.append(f"  ... 还有约 {len(raw_elements) - max_elements} 个元素未显示")

    # iframe 元素
    if has_iframe_elements:
        iframe_start = len(elements)
        current_frame = ""
        lines.append("")

        for j, el in enumerate(iframe_elements):
            frame_name = el.get("_frame_name", "iframe")
            frame_url = el.get("_frame_url", "")

            if frame_name != current_frame:
                current_frame = frame_name
                lines.append(f"  --- iframe: {frame_name} ---")

            ref = f"e{iframe_start + j + 1}"
            role = el.get("role", "")
            name = (el.get("name") or "").strip()
            if not name:
                continue
            label = _ROLE_LABELS.get(role, role)

            parts = [f"  [{ref}] {label} \"{name}\" (iframe)"]
            states = []
            if el.get("value"):
                states.append(f"值=\"{el['value'][:20]}\"")
            if el.get("disabled"):
                states.append("禁用")
            if el.get("required"):
                states.append("必填")
            if states:
                parts.append(f"  ({', '.join(states)})")
            lines.append("".join(parts))

            ref_info = {
                "role": role, "name": name, "nth": el.get("nth"),
                "_frame_url": frame_url,
            }
            if el.get("css"):
                ref_info["css"] = el["css"]
            ref_map[ref] = ref_info

    return "\n".join(lines), ref_map


# ==================== 元素定位 ====================


def _to_ai_friendly_error(exc: Exception, ref: str = "") -> str:
    """将 Playwright 异常转换为 AI 可理解的提示"""
    msg = str(exc)
    if "Element is not visible" in msg or "element is not visible" in msg:
        return f"元素 {ref} 被遮挡或隐藏，可能需要滚动页面或关闭弹窗"
    if "strict mode violation" in msg or "resolved to" in msg:
        return f"元素 {ref} 匹配到多个结果，请使用更精确的定位方式（CSS/XPath）"
    if "Timeout" in msg or "timeout" in msg:
        return f"操作超时，元素 {ref} 可能还未加载完成，尝试 browser_wait 等待后重试"
    if "Element is outside" in msg:
        return f"元素 {ref} 在可视区域外，请先 browser_scroll 滚动到该元素"
    if "detached" in msg:
        return f"元素 {ref} 已从页面移除（页面可能已更新），请 browser_snapshot 重新扫描"
    return f"操作元素 {ref} 失败: {msg}"


def _resolve_ref(page, ref: str, ref_map: dict):
    """将元素引用解析为 Playwright locator。支持: eN 编号 / CSS 选择器 / XPath"""
    if _REF_PATTERN.match(ref) and ref in ref_map:
        info = ref_map[ref]
        if info.get("css"):
            return page.locator(info["css"])
        role, name = info["role"], info["name"]
        locator = page.get_by_role(role, name=name, exact=True)
        nth = info.get("nth")
        if nth is not None and nth > 0:
            locator = locator.nth(nth)
        else:
            locator = locator.first
        return locator

    if _XPATH_PATTERN.match(ref):
        return page.locator(f"xpath={ref}")

    return page.locator(ref)


def _find_frame_for_ref(page, ref_info: dict):
    """根据 ref_info 中的 _frame_url 找到对应的 frame"""
    frame_url = ref_info.get("_frame_url", "")
    if not frame_url:
        return page

    for frame in page.frames:
        if frame.url == frame_url:
            return frame

    frame_domain = urlparse(frame_url).netloc
    for frame in page.frames:
        if frame_domain and frame_domain in frame.url:
            return frame

    return page


async def _smart_locate(page, ref: str, ref_map: dict, action: str = "click"):
    """
    智能元素定位：多策略重试，支持 iframe 内元素。

    尝试顺序:
    1. ref_map 精确定位（检测是否在 iframe 内）
    2. 文本匹配（get_by_text）
    3. Placeholder 匹配
    4. Label 匹配
    5. Role + name 模糊匹配
    6. CSS 选择器
    7. 遍历所有 frame 查找
    """
    if _REF_PATTERN.match(ref) and ref in ref_map:
        info = ref_map[ref]
        target_frame = _find_frame_for_ref(page, info)

        locator = _resolve_ref(target_frame, ref, ref_map)
        try:
            if await locator.count() > 0:
                return locator
        except Exception:
            pass

        name = info.get("name", "")
        role = info.get("role", "")

        search_frames = [target_frame]
        if target_frame == page and len(page.frames) > 1:
            search_frames = page.frames

        for frame in search_frames:
            try:
                text_loc = frame.get_by_text(name, exact=False).first
                if await text_loc.count() > 0:
                    return text_loc
            except Exception:
                pass

            if role == "textbox":
                try:
                    ph_loc = frame.get_by_placeholder(name).first
                    if await ph_loc.count() > 0:
                        return ph_loc
                except Exception:
                    pass

            try:
                label_loc = frame.get_by_label(name).first
                if await label_loc.count() > 0:
                    return label_loc
            except Exception:
                pass

            if role in ("button", "link", "tab", "menuitem", "textbox",
                        "option", "combobox", "listitem", "searchbox", "checkbox", "radio"):
                try:
                    role_loc = frame.get_by_role(role, name=re.compile(re.escape(name[:20]), re.IGNORECASE)).first
                    if await role_loc.count() > 0:
                        return role_loc
                except Exception:
                    pass

            if info.get("css"):
                try:
                    css_loc = frame.locator(info["css"])
                    if await css_loc.count() > 0:
                        return css_loc
                except Exception:
                    pass

        raise Exception(f"元素 {ref} (\"{name}\") 无法定位，页面可能已变化，请重新 snapshot")

    all_frames = page.frames if hasattr(page, 'frames') else [page]

    for frame in all_frames:
        if _XPATH_PATTERN.match(ref):
            try:
                locator = frame.locator(f"xpath={ref}")
                if await locator.count() > 0:
                    return locator
            except Exception:
                pass
            continue

        try:
            locator = frame.locator(ref)
            if await locator.count() > 0:
                return locator
        except Exception:
            pass

        try:
            text_loc = frame.get_by_text(ref, exact=False).first
            if await text_loc.count() > 0:
                return text_loc
        except Exception:
            pass

        try:
            ph_loc = frame.get_by_placeholder(ref).first
            if await ph_loc.count() > 0:
                return ph_loc
        except Exception:
            pass

    raise Exception(f"找不到元素: {ref}（已搜索主页面和 {len(all_frames) - 1} 个 iframe）")


# ==================== 持久浏览器会话 ====================

from ..skills.registry import register, SkillResult

_feishu_channel = None


def set_feishu_channel(channel):
    global _feishu_channel
    _feishu_channel = channel


class _SharedBrowser:
    """
    v2.0 增强版持久浏览器管理器
    
    新增功能:
    - 操作历史追踪 (_action_history)
    - 页面状态跟踪 (_page_states)
    - 智能操作去重
    """
    _browser: PlaywrightBrowser | None = None
    _idle_task: asyncio.Task | None = None
    _ref_map: dict = {}
    _last_snapshot_time: float = 0
    _network_log: list = []
    _network_monitoring: bool = False
    _llm_router = None
    _action_history: list = []  # v2.0: 操作历史
    _page_states: dict = {}     # v2.0: 页面状态记录 {url: {title, elements_count, timestamp}}
    IDLE_TIMEOUT = 300
    MAX_NETWORK_LOG = 200
    MAX_ACTION_HISTORY = 50     # v2.0: 最多保留50条操作记录

    @classmethod
    def set_llm_router(cls, router):
        cls._llm_router = router

    @classmethod
    def record_action(cls, action_type: str, target: str, result: str, success: bool):
        """v2.0: 记录操作历史"""
        cls._action_history.append({
            "type": action_type,
            "target": target,
            "result": result[:200],
            "success": success,
            "timestamp": time.time(),
            "url": cls._browser._page.url if cls._browser and cls._browser._page else "",
        })
        # 限制历史记录长度
        if len(cls._action_history) > cls.MAX_ACTION_HISTORY:
            cls._action_history = cls._action_history[-cls.MAX_ACTION_HISTORY:]

    @classmethod
    def get_recent_actions(cls, count: int = 10) -> list:
        """v2.0: 获取最近的操作记录"""
        return cls._action_history[-count:]

    @classmethod
    def is_duplicate_action(cls, action_type: str, target: str) -> bool:
        """v2.0: 检查是否重复操作（同一页面上5秒内的相同操作）"""
        if not cls._action_history:
            return False
        
        current_url = cls._browser._page.url if cls._browser and cls._browser._page else ""
        now = time.time()
        
        for action in reversed(cls._action_history[-5:]):
            if (action["type"] == action_type and 
                action["target"] == target and 
                action["url"] == current_url and
                now - action["timestamp"] < 5):
                return True
        return False

    @classmethod
    def record_page_state(cls, url: str, title: str, elements_count: int):
        """v2.0: 记录页面状态"""
        cls._page_states[url] = {
            "title": title,
            "elements_count": elements_count,
            "timestamp": time.time(),
        }
        # 限制状态记录数量
        if len(cls._page_states) > 20:
            oldest_url = min(cls._page_states, key=lambda k: cls._page_states[k]["timestamp"])
            del cls._page_states[oldest_url]

    @classmethod
    def clear_session(cls):
        """v2.0: 清理会话数据"""
        cls._action_history = []
        cls._page_states = {}
        cls._ref_map = {}
        cls._network_log = []

    @classmethod
    async def get(cls) -> PlaywrightBrowser:
        if cls._browser is not None and cls._browser._page is not None:
            try:
                await cls._browser._page.title()
            except Exception:
                logger.warning("持久浏览器会话已失效，重新启动")
                await cls._close_quietly()

        if cls._browser is None or cls._browser._page is None:
            await cls._close_quietly()
            cls._browser = PlaywrightBrowser(
                headless=os.environ.get("BROWSER_HEADLESS", "true").lower() == "true",
                browser_mode=os.environ.get("BROWSER_MODE", "auto"),
                cdp_port=int(os.environ.get("BROWSER_CDP_PORT", "9222")),
                executable_path=os.environ.get("BROWSER_EXECUTABLE_PATH", ""),
            )
            try:
                await cls._browser.start()
            except Exception:
                cls._browser = None
                raise
            logger.info("持久浏览器会话已启动")
        cls._reset_idle()
        return cls._browser

    @classmethod
    def is_active(cls) -> bool:
        return cls._browser is not None and cls._browser._page is not None

    @classmethod
    def _reset_idle(cls):
        if cls._idle_task and not cls._idle_task.done():
            cls._idle_task.cancel()
        cls._idle_task = asyncio.create_task(cls._idle_countdown())

    @classmethod
    async def _idle_countdown(cls):
        await asyncio.sleep(cls.IDLE_TIMEOUT)
        logger.info(f"浏览器空闲 {cls.IDLE_TIMEOUT}s，自动关闭")
        await cls._close_quietly()

    @classmethod
    async def close(cls):
        await cls._close_quietly()

    @classmethod
    async def start_network_monitor(cls, url_filter: str = ""):
        browser = await cls.get()
        if cls._network_monitoring:
            return
        cls._network_log = []
        cls._network_monitoring = True

        async def _on_response(response):
            if not cls._network_monitoring:
                return
            url = response.url
            if url_filter and url_filter not in url:
                return
            resource_type = response.request.resource_type
            if resource_type not in ("xhr", "fetch", "document", "script"):
                return
            try:
                content_type = response.headers.get("content-type", "")
                body_text = ""
                if "json" in content_type or "javascript" in content_type or "text" in content_type:
                    try:
                        raw = await response.body()
                        body_text = raw.decode("utf-8", errors="replace")[:8000]
                    except Exception:
                        body_text = "(无法读取)"

                entry = {
                    "url": url[:200],
                    "status": response.status,
                    "method": response.request.method,
                    "type": resource_type,
                    "content_type": content_type[:80],
                    "body_preview": body_text[:3000] if body_text else "",
                    "time": time.time(),
                }
                if len(cls._network_log) < cls.MAX_NETWORK_LOG:
                    cls._network_log.append(entry)
            except Exception:
                pass

        browser._page.on("response", _on_response)

    @classmethod
    def stop_network_monitor(cls):
        cls._network_monitoring = False

    @classmethod
    def get_network_log(cls, url_contains: str = "", content_type: str = "",
                        limit: int = 30) -> list[dict]:
        results = []
        for entry in reversed(cls._network_log):
            if url_contains and url_contains not in entry["url"]:
                continue
            if content_type and content_type not in entry.get("content_type", ""):
                continue
            results.append(entry)
            if len(results) >= limit:
                break
        return results

    @classmethod
    async def _close_quietly(cls):
        cls._network_monitoring = False
        cls._network_log = []
        if cls._idle_task and not cls._idle_task.done():
            cls._idle_task.cancel()
            cls._idle_task = None
        if cls._browser:
            try:
                await cls._browser.stop()
            except Exception as e:
                logger.warning(f"关闭持久浏览器异常: {e}")
            cls._browser = None
            cls._ref_map = {}
            cls._last_snapshot_time = 0

    _last_snapshot_info: str = ""
    _snapshot_url: str = ""
    SNAPSHOT_CACHE_TTL: float = 1.5

    @classmethod
    async def do_snapshot(cls, force: bool = False) -> tuple[str, dict]:
        browser = await cls.get()
        now = time.time()
        current_url = browser._page.url if browser._page else ""
        if (not force
                and cls._ref_map
                and cls._last_snapshot_info
                and current_url == cls._snapshot_url
                and (now - cls._last_snapshot_time) < cls.SNAPSHOT_CACHE_TTL):
            return cls._last_snapshot_info, cls._ref_map
        info, ref_map = await _snapshot_elements(browser)
        cls._ref_map = ref_map
        cls._last_snapshot_time = now
        cls._last_snapshot_info = info
        cls._snapshot_url = current_url
        return info, ref_map


# ==================== 工具注册 ====================

def _not_active_error() -> SkillResult:
    return SkillResult(success=False, error="浏览器未打开，请先调用 browser_open 打开网页")


@register(
    name="browser_open",
    description=(
        "在浏览器中打开网页。自动加载该网站已保存的 Cookie（保持登录状态）。"
        "返回页面元素快照：每个可交互元素有编号 [e1] [e2]...，后续用编号操作。"
        "浏览器保持打开，适合多步任务。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要打开的网址"},
        },
        "required": ["url"],
    },
    risk_level="low",
)
async def browser_open(url: str) -> SkillResult:
    try:
        browser = await _SharedBrowser.get()
        result = await browser.goto(url)
        if not result.success:
            return SkillResult(success=False, error=f"打开失败: {result.error}")

        info, ref_map = await _SharedBrowser.do_snapshot()

        # 元素太少可能是页面还在加载，再等一轮
        if len(ref_map) < 2:
            await browser.wait_for_page_ready(timeout=5)
            info, ref_map = await _SharedBrowser.do_snapshot()

        return SkillResult(success=True, data=info)
    except ImportError as e:
        return SkillResult(success=False, error=str(e))
    except Exception as e:
        return SkillResult(success=False, error=f"打开网页失败: {e}")


@register(
    name="browser_navigate",
    description=(
        "在已打开的浏览器中导航到新 URL（不关闭浏览器）。"
        "与 browser_open 的区别：browser_open 适合首次打开，browser_navigate 适合会话中跳转。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要导航到的网址"},
        },
        "required": ["url"],
    },
    risk_level="low",
)
async def browser_navigate(url: str) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        await browser.load_cookies_for_url(url)
        await browser._page.goto(url, wait_until="domcontentloaded")
        await browser.wait_for_page_ready(timeout=8)

        info, _ = await _SharedBrowser.do_snapshot(force=True)
        return SkillResult(success=True, data=info)
    except Exception as e:
        return SkillResult(success=False, error=f"导航失败: {e}")


@register(
    name="browser_snapshot",
    description=(
        "重新扫描当前页面的可交互元素，刷新元素编号。"
        "在以下情况必须调用：页面内容动态变化后、AJAX 加载后、不确定元素编号是否还有效时。"
    ),
    parameters={
        "type": "object",
        "properties": {},
    },
    risk_level="low",
)
async def browser_snapshot() -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        info, _ = await _SharedBrowser.do_snapshot(force=True)
        return SkillResult(success=True, data=info)
    except Exception as e:
        return SkillResult(success=False, error=f"快照失败: {e}")


async def _dismiss_blocking_overlay(browser) -> bool:
    """
    尝试关闭遮挡操作的登录弹窗/遮罩层。
    优化版：用单次 JS 检测 + 关闭，减少网络往返。
    """
    page = browser._page
    try:
        result = await page.evaluate("""() => {
            const overlaySels = [
                '[id*="login-full-panel"]', '[id*="login-panel"]',
                '[class*="login-full-panel"]', '[class*="loginPanel"]',
                '[class*="passport-sdk"]', '[id*="passport-sdk"]',
                '[class*="dy-account"]', 'dialog[open]', '[role="dialog"]',
                '.modal.show', '.ant-modal-wrap:not([style*="display: none"])',
                '[class*="login-dialog"]', '[class*="login-modal"]', '[class*="SignFlow"]',
            ];
            const closeSels = [
                '[class*="close"]', '[aria-label="Close"]', '[aria-label="关闭"]',
                'button:has(svg)', '.close-btn', '.btn-close',
                '[class*="icon-close"]', '[class*="iconClose"]',
            ];
            for (const oSel of overlaySels) {
                const overlay = document.querySelector(oSel);
                if (!overlay) continue;
                const r = overlay.getBoundingClientRect();
                const s = window.getComputedStyle(overlay);
                if (r.width < 50 || r.height < 50 || s.display === 'none' || s.visibility === 'hidden') continue;
                for (const cSel of closeSels) {
                    const btn = overlay.querySelector(cSel);
                    if (btn) { btn.click(); return 'clicked'; }
                }
                overlay.remove();
                return 'removed';
            }
            // 清理全屏遮罩
            let removed = 0;
            document.querySelectorAll('[class*="mask"], [class*="overlay"]').forEach(el => {
                const st = window.getComputedStyle(el);
                if (st.position === 'fixed' && parseFloat(st.opacity) < 1) { el.remove(); removed++; }
            });
            document.querySelectorAll('div[style]').forEach(el => {
                const st = window.getComputedStyle(el);
                if (st.position === 'fixed' && parseInt(st.zIndex) > 999 &&
                    el.offsetWidth >= window.innerWidth * 0.8 &&
                    el.offsetHeight >= window.innerHeight * 0.8) { el.remove(); removed++; }
            });
            return removed > 0 ? 'removed' : 'none';
        }""")
        if result and result != 'none':
            await asyncio.sleep(0.3)
            return True
        return False
    except Exception as e:
        logger.debug(f"关闭遮挡弹窗失败: {e}")
        return False


async def _action_with_stale_recovery(browser, ref: str, action_fn, action_name: str) -> SkillResult:
    """
    v2.0 增强版：带失效引用自动恢复的操作包装器。
    
    恢复策略：
    0. 操作前主动检测并关闭遮挡弹窗（避免 30s 超时浪费）
    1. 首先尝试直接执行
    2. 失败后重新快照，根据 role+name+nth 精确匹配
    3. 精确匹配失败则尝试模糊匹配（仅 role+name）
    4. 仍然失败则尝试文本匹配
    5. 提供智能的错误提示和恢复建议
    """
    try:
        return await action_fn(ref, _SharedBrowser._ref_map)
    except Exception as first_err:
        error_str = str(first_err).lower()
        ref_info = _SharedBrowser._ref_map.get(ref)
        
        if not ref_info or not _REF_PATTERN.match(ref):
            return SkillResult(success=False, error=_to_ai_friendly_error(first_err, ref))

        logger.info(f"操作 {action_name}({ref}) 失败，自动重新快照重试: {first_err}")

        # v2.0: 根据错误类型决定恢复策略
        need_wait = "not visible" in error_str or "hidden" in error_str or "detached" in error_str
        is_strict_violation = "strict mode violation" in error_str
        is_intercepted = "intercepts pointer" in error_str
        
        if is_intercepted:
            dismissed = await _dismiss_blocking_overlay(browser)
            if dismissed:
                logger.info("已自动关闭遮挡弹窗，直接重试原操作")
                try:
                    return await action_fn(ref, _SharedBrowser._ref_map)
                except Exception:
                    logger.info("关闭弹窗后原引用仍失败，继续重新快照")
            else:
                logger.info("未能关闭遮挡弹窗，尝试重新快照恢复")

        if need_wait:
            await asyncio.sleep(0.5)
            await browser.wait_for_page_ready(timeout=3)
        
        try:
            _, new_ref_map = await _SharedBrowser.do_snapshot()

            old_role = ref_info.get("role", "")
            old_name = ref_info.get("name", "")
            old_nth = ref_info.get("nth")
            
            # 策略1: 精确匹配 role + name + nth
            new_ref = None
            fallback_ref = None
            name_matches = []
            
            for r, info in new_ref_map.items():
                if info.get("role") == old_role and info.get("name") == old_name:
                    name_matches.append(r)
                    if info.get("nth") == old_nth:
                        new_ref = r
                        break
                    if fallback_ref is None:
                        fallback_ref = r
            
            if not new_ref:
                new_ref = fallback_ref
            
            # v2.0: 如果是 strict mode violation，明确选择第一个匹配
            if is_strict_violation and len(name_matches) > 1:
                new_ref = name_matches[0]
                logger.info(f"strict mode violation: 选择第一个匹配 {new_ref}")

            if new_ref:
                logger.info(f"自动恢复: {ref} -> {new_ref} (role={old_role}, name={old_name})")
                try:
                    return await action_fn(new_ref, new_ref_map)
                except Exception as second_err:
                    # v2.0: 第二次也失败，尝试文本匹配
                    if old_name:
                        try:
                            text_locator = browser._page.get_by_text(old_name, exact=False).first
                            if await text_locator.count() > 0:
                                logger.info(f"文本匹配恢复: {old_name}")
                                # 直接操作文本定位器
                                if action_name == "click":
                                    await text_locator.click(timeout=10000)
                                    await browser.wait_for_page_ready(timeout=5)
                                    info, _ = await _SharedBrowser.do_snapshot()
                                    return SkillResult(success=True, data=f"✓ 已点击 (文本匹配)\n\n{info}")
                        except Exception:
                            pass
                    
                    return SkillResult(success=False, error=_to_ai_friendly_error(second_err, new_ref))
            else:
                # v2.0: 提供智能恢复建议
                suggestions = []
                if "login" in old_name.lower() or "登录" in old_name:
                    suggestions.append("检查是否已登录成功，页面可能已跳转")
                if "submit" in old_name.lower() or "提交" in old_name:
                    suggestions.append("表单可能已提交，检查是否有成功/错误提示")
                if old_role == "textbox":
                    suggestions.append("输入框可能在弹窗中，尝试先检测并关闭弹窗")
                
                suggestions.append("尝试调用 browser_snapshot 获取最新元素列表")
                suggestions.append("尝试调用 browser_check_state 检查当前页面状态")
                
                error_msg = (
                    f"元素 {ref} (\"{old_name}\") 在重新扫描后未找到，页面可能已变化。\n\n"
                    f"💡 建议操作:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(suggestions))
                )
                
                # 返回最新的快照信息帮助 Agent 决策
                info, _ = await _SharedBrowser.do_snapshot()
                
                return SkillResult(
                    success=False,
                    error=f"{error_msg}\n\n--- 当前页面 ---\n{info[:2000]}"
                )
        except Exception as retry_err:
            return SkillResult(success=False, error=_to_ai_friendly_error(retry_err, ref))


@register(
    name="browser_click",
    description=(
        "点击页面元素（按钮、链接、标签页等）。\n"
        "⚠️ 不要用于下拉框/select/combobox，请用 browser_select 代替。\n"
        "参数是元素编号（如 e1）、CSS 选择器、XPath 或按钮文字。"
        "点击后自动刷新元素列表。如果元素找不到会自动重新扫描并重试。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "元素编号（如 e1）、CSS 选择器、XPath 或元素文字"},
            "wait_after": {"type": "number", "description": "点击后额外等待秒数（默认自动判断）"},
            "rpa_mode": {"type": "boolean", "description": "是否使用 RPA 丝滑模式（默认 true）"},
        },
        "required": ["ref"],
    },
    risk_level="low",
)
async def browser_click(ref: str, wait_after: float = None, rpa_mode: bool = True) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()

    browser = await _SharedBrowser.get()
    
    # 智能检测：如果是 combobox/select 元素，提示使用 browser_select
    ref_info = _SharedBrowser._ref_map.get(ref, {})
    ref_role = ref_info.get("role", "")
    if ref_role in ("combobox", "select"):
        logger.info(f"browser_click 检测到 {ref} 是下拉框({ref_role})，提示使用 browser_select")
        return SkillResult(
            success=False,
            error=f"⚠️ 元素 {ref} 是下拉框（{ref_role}），请使用 browser_select(\"{ref}\", value=\"目标选项\") 来操作。\n"
                  f"browser_select 会自动展开下拉框、滚动查找选项并点击选中，比 browser_click 更可靠。"
        )
    
    # v2.0: 检测重复操作
    if _SharedBrowser.is_duplicate_action("click", ref):
        logger.warning(f"检测到重复点击: {ref}，可能导致死循环")
        return SkillResult(
            success=False,
            error=f"⚠️ 检测到5秒内重复点击 {ref}，已阻止。\n"
                  f"如果页面没有变化，可能需要尝试其他操作方式，如：\n"
                  f"  1. 使用 browser_check_state 检查当前状态\n"
                  f"  2. 使用 browser_wait_for 等待元素变化\n"
                  f"  3. 尝试点击其他元素"
        )

    async def _do_click(r: str, rmap: dict) -> SkillResult:
        old_url = browser._page.url
        old_title = await browser._page.title()
        
        locator = await _smart_locate(browser._page, r, rmap, "click")
        
        # 快速检查元素可交互
        try:
            await locator.wait_for(state="visible", timeout=3000)
            if not await locator.is_enabled():
                _SharedBrowser.record_action("click", r, "元素禁用", False)
                return SkillResult(
                    success=False, 
                    error=f"元素 {r} 当前处于禁用状态，无法点击。请检查是否需要先完成其他操作。"
                )
        except Exception as e:
            logger.debug(f"等待元素可见超时: {e}")
        
        if rpa_mode and RPAConfig.enabled:
            await browser.rpa_move_mouse_to(locator)
            await locator.click(timeout=8000)
        else:
            await locator.click(timeout=8000)
        
        if wait_after is not None:
            await asyncio.sleep(wait_after)
        
        new_url = browser._page.url
        url_changed = new_url != old_url
        
        if url_changed:
            await browser.wait_for_page_ready(timeout=8)
            change_info = f"页面已跳转: {new_url}"
        else:
            await browser.wait_for_page_ready(timeout=3)
            new_title = await browser._page.title()
            if new_title != old_title:
                change_info = f"页面标题变化: {old_title} → {new_title}"
            else:
                change_info = "页面内容可能已更新"

        info, _ = await _SharedBrowser.do_snapshot(force=url_changed)
        
        # v2.0: 记录成功操作
        _SharedBrowser.record_action("click", r, change_info, True)
        
        rpa_tag = " 🖱️" if rpa_mode and RPAConfig.enabled else ""
        return SkillResult(success=True, data=f"✓ 已点击 {r}{rpa_tag}\n{change_info}\n\n{info}")

    result = await _action_with_stale_recovery(browser, ref, _do_click, "click")
    
    # v2.0: 记录失败操作
    if not result.success:
        _SharedBrowser.record_action("click", ref, result.error[:100], False)
    
    return result


@register(
    name="browser_type",
    description=(
        "在输入框中输入文字。ref 是元素编号（如 e1）、CSS 选择器、XPath、或 placeholder 文本。"
        "设置 press_enter=true 可按回车提交（如搜索框）。"
        "设置 clear=true 会先清空输入框再输入。"
        "v3.0 RPA 模式：元素高亮 + 平滑移动鼠标 + 人类化逐字输入（随机延迟）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "输入框编号（如 e1）、CSS 选择器、XPath 或 placeholder"},
            "text": {"type": "string", "description": "要输入的文字"},
            "press_enter": {"type": "boolean", "description": "输入后是否按回车（默认 false）"},
            "clear": {"type": "boolean", "description": "是否先清空输入框（默认 true）"},
            "rpa_mode": {"type": "boolean", "description": "是否使用 RPA 丝滑模式（默认 true）"},
        },
        "required": ["ref", "text"],
    },
    risk_level="low",
)
async def browser_type(ref: str, text: str, press_enter: bool = False, clear: bool = True, rpa_mode: bool = True) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()

    browser = await _SharedBrowser.get()

    async def _do_type(r: str, rmap: dict) -> SkillResult:
        old_url = browser._page.url
        locator = await _smart_locate(browser._page, r, rmap, "fill")

        try:
            await locator.wait_for(state="visible", timeout=3000)
            if not await locator.is_editable():
                return SkillResult(
                    success=False,
                    error=f"输入框 {r} 当前不可编辑，可能需要先点击激活或等待页面加载。"
                )
        except Exception as e:
            logger.debug(f"等待输入框可见超时: {e}")

        # v3.0 RPA: 平滑移动 + 人类化输入（绕过反爬）
        if rpa_mode and RPAConfig.enabled:
            # 1. 平滑移动鼠标到输入框
            await browser.rpa_move_mouse_to(locator)
            
            # 2. 点击聚焦
            await locator.click()
            await asyncio.sleep(0.05)
            
            # 3. 清空
            if clear:
                await locator.fill("")
            
            # 4. 人类化逐字输入（随机延迟，绕过输入速度检测）
            for char in text:
                await locator.type(char, delay=0)
                delay = _get_human_type_delay()
                await asyncio.sleep(delay / 1000)
        else:
            # 非 RPA 模式：快速输入
            try:
                await locator.focus()
                await asyncio.sleep(0.1)
            except Exception:
                pass

            if clear:
                await locator.fill("")
            await locator.type(text, delay=30)

        # v2.0: 验证输入是否成功
        try:
            actual_value = await locator.input_value()
            if text not in actual_value and actual_value not in text:
                logger.warning(f"输入验证: 期望包含 '{text[:20]}', 实际值 '{actual_value[:20]}'")
        except Exception:
            pass

        if press_enter:
            await locator.press("Enter")
            await asyncio.sleep(0.2)
            new_url = browser._page.url
            url_changed = new_url != old_url
            if url_changed:
                await browser.wait_for_page_ready(timeout=8)
            else:
                await browser.wait_for_page_ready(timeout=3)

        info, _ = await _SharedBrowser.do_snapshot(force=press_enter)
        
        result_msg = f"✓ 已输入: {text[:50]}{'...' if len(text) > 50 else ''}"
        if press_enter:
            result_msg += " (已按回车)"
        
        return SkillResult(success=True, data=f"{result_msg}\n\n{info}")

    return await _action_with_stale_recovery(browser, ref, _do_type, "type")


@register(
    name="browser_fill_form",
    description=(
        "一次性填写多个表单字段。每个字段指定 ref 和 value。\n"
        "自动识别字段类型：输入框用输入，下拉框用 select，复选框用切换。\n"
        "比多次单独调用更高效，适合登录表单、注册表单、信息填写等。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "fields": {
                "type": "array",
                "description": "要填写的字段列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string", "description": "元素编号或选择器"},
                        "value": {"type": "string", "description": "要填写/选择的值"},
                    },
                    "required": ["ref", "value"],
                },
            },
            "submit_ref": {"type": "string", "description": "填完后要点击的提交按钮编号（可选）"},
        },
        "required": ["fields"],
    },
    risk_level="low",
)
async def browser_fill_form(fields: list[dict], submit_ref: str = "") -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        results = []
        need_resnapshot = False

        for i, f in enumerate(fields, 1):
            ref = f.get("ref", "")
            value = f.get("value", "")
            
            # 检查元素类型来决定操作方式
            ref_info = _SharedBrowser._ref_map.get(ref, {})
            ref_role = ref_info.get("role", "")
            
            try:
                if ref_role in ("combobox", "select"):
                    # 下拉框 → 用 browser_select
                    sel_result = await browser_select(ref, value=value)
                    if sel_result.success:
                        results.append(f"  {i}. {ref} = \"{value[:20]}\" ✓ (下拉选择)")
                    else:
                        results.append(f"  {i}. {ref} 选择失败: {(sel_result.error or '')[:60]}")
                    continue
                
                if ref_role in ("checkbox", "switch", "radio"):
                    # 切换类 → 用 browser_interact
                    int_result = await browser_interact(ref, value=value)
                    if int_result.success:
                        results.append(f"  {i}. {ref} = {value} ✓ (切换)")
                    else:
                        results.append(f"  {i}. {ref} 切换失败: {(int_result.error or '')[:60]}")
                    continue
                
                # 默认：输入框 → fill + type
                rmap = _SharedBrowser._ref_map
                locator = await _smart_locate(browser._page, ref, rmap, "fill")
                
                # 检查是否实际是 <select> 标签（ref_map 中可能没正确识别）
                try:
                    tag = await locator.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "select":
                        sel_result = await browser_select(ref, value=value)
                        if sel_result.success:
                            results.append(f"  {i}. {ref} = \"{value[:20]}\" ✓ (下拉选择)")
                        else:
                            results.append(f"  {i}. {ref} 选择失败: {(sel_result.error or '')[:60]}")
                        continue
                except Exception:
                    pass
                
                await locator.fill("")
                if RPAConfig.enabled:
                    for ch in value:
                        await locator.type(ch, delay=0)
                        await asyncio.sleep(_get_human_type_delay() / 1000)
                else:
                    await locator.type(value, delay=20)
                results.append(f"  {i}. {ref} = \"{value[:20]}\" ✓")
                
            except Exception as e:
                if not need_resnapshot:
                    logger.info(f"表单填写 {ref} 失败，尝试重新快照: {e}")
                    await _SharedBrowser.do_snapshot()
                    need_resnapshot = True
                    try:
                        locator = await _smart_locate(browser._page, ref, _SharedBrowser._ref_map, "fill")
                        await locator.fill("")
                        await locator.type(value, delay=20)
                        results.append(f"  {i}. {ref} = \"{value[:20]}\" ✓ (重试)")
                        continue
                    except Exception:
                        pass
                results.append(f"  {i}. {ref} 填写失败: {_to_ai_friendly_error(e, ref)}")

        if submit_ref:
            await asyncio.sleep(0.2)
            try:
                locator = await _smart_locate(browser._page, submit_ref, _SharedBrowser._ref_map, "click")
                if RPAConfig.enabled:
                    await browser.rpa_move_mouse_to(locator)
                await locator.click(timeout=8000)
                results.append(f"  已点击提交: {submit_ref} ✓")
                await browser.wait_for_page_ready(timeout=6)
            except Exception as e:
                results.append(f"  提交点击失败: {_to_ai_friendly_error(e, submit_ref)}")

        info, _ = await _SharedBrowser.do_snapshot(force=bool(submit_ref))
        return SkillResult(success=True, data="表单填写结果:\n" + "\n".join(results) + f"\n\n{info}")
    except Exception as e:
        return SkillResult(success=False, error=f"表单填写失败: {e}")


@register(
    name="browser_wait_for",
    description=(
        "等待某个元素出现或消失。适用于：\n"
        "- 等待登录后页面跳转\n"
        "- 等待弹窗出现\n"
        "- 等待加载完成（loading 消失）\n"
        "- 等待某个按钮变为可点击\n"
        "返回等待结果和最新的页面快照。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "要等待的元素编号、CSS 选择器或文本"},
            "state": {
                "type": "string",
                "description": "期望的状态",
                "enum": ["visible", "hidden", "attached", "detached"],
                "default": "visible"
            },
            "timeout": {"type": "number", "description": "超时时间（秒），默认 10"},
        },
        "required": ["ref"],
    },
    risk_level="low",
)
async def browser_wait_for(ref: str, state: str = "visible", timeout: float = 10) -> SkillResult:
    """等待元素出现或消失"""
    if not _SharedBrowser.is_active():
        return _not_active_error()
    
    try:
        browser = await _SharedBrowser.get()
        
        # 尝试定位元素
        try:
            locator = await _smart_locate(browser._page, ref, _SharedBrowser._ref_map, "wait")
        except Exception:
            # 如果是等待消失，元素不存在就是成功
            if state in ("hidden", "detached"):
                info, _ = await _SharedBrowser.do_snapshot()
                return SkillResult(success=True, data=f"✓ 元素 {ref} 已不存在\n\n{info}")
            # 否则尝试用文本查找
            locator = browser._page.get_by_text(ref, exact=False).first
        
        state_map = {
            "visible": "visible",
            "hidden": "hidden",
            "attached": "attached",
            "detached": "detached",
        }
        playwright_state = state_map.get(state, "visible")
        
        try:
            await locator.wait_for(state=playwright_state, timeout=timeout * 1000)
            
            info, _ = await _SharedBrowser.do_snapshot()
            
            state_desc = {
                "visible": "已出现并可见",
                "hidden": "已隐藏",
                "attached": "已加载到 DOM",
                "detached": "已从 DOM 移除",
            }.get(state, state)
            
            return SkillResult(success=True, data=f"✓ 元素 {ref} {state_desc}\n\n{info}")
            
        except Exception as e:
            info, _ = await _SharedBrowser.do_snapshot()
            return SkillResult(
                success=False,
                error=f"等待超时 ({timeout}s): 元素 {ref} 未达到 {state} 状态\n\n当前页面:\n{info}"
            )
            
    except Exception as e:
        return SkillResult(success=False, error=f"等待失败: {e}")


@register(
    name="browser_check_state",
    description=(
        "检查当前页面状态，不执行任何操作。用于：\n"
        "- 确认登录是否成功（检查是否有用户头像、退出按钮等）\n"
        "- 确认操作是否生效\n"
        "- 检查是否有错误提示\n"
        "- 检查页面是否加载完成"
    ),
    parameters={
        "type": "object",
        "properties": {
            "check_text": {
                "type": "string",
                "description": "要检查的文本（检查页面是否包含此文本）"
            },
            "check_element": {
                "type": "string",
                "description": "要检查的元素（检查元素是否存在）"
            },
        },
        "required": [],
    },
    risk_level="low",
)
async def browser_check_state(check_text: str = None, check_element: str = None) -> SkillResult:
    """检查页面状态"""
    if not _SharedBrowser.is_active():
        return _not_active_error()
    
    try:
        browser = await _SharedBrowser.get()
        
        results = []
        
        # 基本信息
        title = await browser._page.title()
        url = browser._page.url
        results.append(f"📄 页面: {title}")
        results.append(f"🔗 URL: {url}")
        
        # 检查文本
        if check_text:
            try:
                content = await browser._page.content()
                if check_text.lower() in content.lower():
                    results.append(f"✓ 页面包含文本: \"{check_text}\"")
                else:
                    results.append(f"✗ 页面不包含文本: \"{check_text}\"")
            except Exception:
                results.append(f"? 无法检查文本")
        
        # 检查元素
        if check_element:
            try:
                locator = await _smart_locate(browser._page, check_element, _SharedBrowser._ref_map, "check")
                count = await locator.count()
                if count > 0:
                    is_visible = await locator.first.is_visible()
                    results.append(f"✓ 元素 {check_element} 存在 (可见: {is_visible})")
                else:
                    results.append(f"✗ 元素 {check_element} 不存在")
            except Exception:
                results.append(f"✗ 元素 {check_element} 未找到")
        
        # 检查是否有常见的错误/成功提示
        error_indicators = ["错误", "失败", "error", "failed", "invalid", "incorrect"]
        success_indicators = ["成功", "欢迎", "success", "welcome", "logged in"]
        
        try:
            page_text = await browser._page.inner_text("body")
            page_text_lower = page_text.lower()
            
            for err in error_indicators:
                if err in page_text_lower:
                    results.append(f"⚠️ 检测到可能的错误提示: \"{err}\"")
                    break
            
            for succ in success_indicators:
                if succ in page_text_lower:
                    results.append(f"✓ 检测到可能的成功提示: \"{succ}\"")
                    break
        except Exception:
            pass
        
        # 检查弹窗
        try:
            dialogs = await browser._page.query_selector_all('[role="dialog"], [role="alertdialog"], .modal, dialog[open]')
            if dialogs:
                results.append(f"📢 检测到 {len(dialogs)} 个弹窗/对话框")
        except Exception:
            pass
        
        info, _ = await _SharedBrowser.do_snapshot()
        
        return SkillResult(
            success=True,
            data="\n".join(results) + f"\n\n--- 页面元素 ---\n{info}"
        )
        
    except Exception as e:
        return SkillResult(success=False, error=f"状态检查失败: {e}")


@register(
    name="browser_scroll",
    description=(
        "滚动当前页面。方向: up/down/top/bottom。滚动后返回更新的元素列表。"
        "v3.0 RPA 模式：平滑滚动动画效果。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "direction": {
                "type": "string", "description": "滚动方向",
                "enum": ["up", "down", "top", "bottom"],
            },
            "amount": {"type": "integer", "description": "滚动像素数（默认 500）"},
            "rpa_mode": {"type": "boolean", "description": "是否使用 RPA 平滑滚动（默认 true）"},
        },
        "required": ["direction"],
    },
    risk_level="low",
)
async def browser_scroll_page(direction: str = "down", amount: int = 500, rpa_mode: bool = True) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        
        # v3.0 RPA: 平滑滚动
        if rpa_mode and RPAConfig.enabled:
            result = await browser.rpa_scroll(direction, amount)
        else:
            result = await browser.scroll(direction, amount)
        
        if not result.success:
            return SkillResult(success=False, error=result.error)

        await asyncio.sleep(0.3)
        info, _ = await _SharedBrowser.do_snapshot()
        rpa_tag = " 📜" if rpa_mode and RPAConfig.enabled else ""
        return SkillResult(success=True, data=f"已滚动 {direction}{rpa_tag}\n\n{info}")
    except Exception as e:
        return SkillResult(success=False, error=f"滚动失败: {e}")


@register(
    name="browser_scroll_to",
    description=(
        "滚动到指定元素位置，让元素出现在可视区域内。用于：\n"
        "- 找到页面下方的元素\n"
        "- 让看不见的选项滚动到可见\n"
        "- 定位到页面中的特定位置"
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "要滚动到的元素编号（如 e5）或 CSS 选择器"},
        },
        "required": ["ref"],
    },
    risk_level="low",
)
async def browser_scroll_to(ref: str) -> SkillResult:
    """滚动到指定元素"""
    if not _SharedBrowser.is_active():
        return _not_active_error()

    try:
        browser = await _SharedBrowser.get()
        locator = await _smart_locate(browser._page, ref, _SharedBrowser._ref_map, "scroll_to")

        await locator.scroll_into_view_if_needed(timeout=5000)
        await asyncio.sleep(0.3)

        info, _ = await _SharedBrowser.do_snapshot()
        return SkillResult(success=True, data=f"✓ 已滚动到 {ref}\n\n{info}")

    except Exception as e:
        return SkillResult(success=False, error=f"滚动到元素失败: {e}")


@register(
    name="browser_back",
    description="浏览器后退到上一个页面。",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
)
async def browser_back() -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        result = await browser.go_back()
        if not result.success:
            return SkillResult(success=False, error=result.error)

        await asyncio.sleep(0.5)
        info, _ = await _SharedBrowser.do_snapshot()
        return SkillResult(success=True, data=f"已后退\n\n{info}")
    except Exception as e:
        return SkillResult(success=False, error=f"后退失败: {e}")


@register(
    name="browser_forward",
    description="浏览器前进到下一个页面。",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
)
async def browser_forward() -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        result = await browser.go_forward()
        if not result.success:
            return SkillResult(success=False, error=result.error)

        await asyncio.sleep(0.5)
        info, _ = await _SharedBrowser.do_snapshot()
        return SkillResult(success=True, data=f"已前进\n\n{info}")
    except Exception as e:
        return SkillResult(success=False, error=f"前进失败: {e}")


@register(
    name="browser_interact",
    description=(
        "智能交互：根据元素类型自动执行合适的操作。\n"
        "- 按钮/链接：自动点击\n"
        "- 输入框：自动填入文本\n"
        "- 复选框/开关：自动切换状态\n"
        "- 下拉框：自动选择选项\n"
        "比单独调用 click/type 更智能，会自动处理常见问题。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "元素编号（如 e1）或 CSS 选择器"},
            "value": {"type": "string", "description": "要输入/选择的值（对于输入框、下拉框必填）"},
        },
        "required": ["ref"],
    },
    risk_level="low",
)
async def browser_interact(ref: str, value: str = None) -> SkillResult:
    """智能交互 - 根据元素类型自动选择操作方式"""
    if not _SharedBrowser.is_active():
        return _not_active_error()
    
    try:
        browser = await _SharedBrowser.get()
        ref_info = _SharedBrowser._ref_map.get(ref, {})
        role = ref_info.get("role", "")
        name = ref_info.get("name", "")
        
        # 根据 role 决定操作方式
        if role in ("button", "link", "tab", "menuitem"):
            # 点击类元素
            return await browser_click(ref)
        
        elif role in ("textbox", "searchbox"):
            if value is None:
                return SkillResult(
                    success=False,
                    error=f"元素 {ref} 是输入框，需要提供 value 参数"
                )
            return await browser_type(ref, value, press_enter=False, clear=True)
        
        elif role == "combobox":
            # 下拉框 — 用专用的 select 工具
            if value is None:
                # 没有 value 就点击展开
                return await browser_click(ref)
            return await browser_select(ref, value=value)
        
        elif role in ("checkbox", "switch", "radio"):
            # 切换类元素
            locator = await _smart_locate(browser._page, ref, _SharedBrowser._ref_map, "interact")
            
            # 检查当前状态
            is_checked = await locator.is_checked()
            
            # 如果有 value 参数，根据 value 决定是否需要切换
            should_check = True
            if value is not None:
                should_check = value.lower() in ("true", "1", "yes", "on", "是")
            
            if is_checked != should_check:
                await locator.click()
                action = "选中" if should_check else "取消选中"
            else:
                action = "状态已是" + ("选中" if is_checked else "未选中")
            
            info, _ = await _SharedBrowser.do_snapshot()
            return SkillResult(success=True, data=f"✓ {action} {ref}\n\n{info}")
        
        elif role == "option":
            # 选项元素 - 直接点击
            return await browser_click(ref)
        
        else:
            # 未知类型 - 尝试点击
            logger.info(f"未知元素类型 {role}，尝试点击")
            return await browser_click(ref)
    
    except Exception as e:
        return SkillResult(success=False, error=f"智能交互失败: {e}")


@register(
    name="browser_wait",
    description=(
        "等待页面上出现特定内容。可以等待元素出现、文字出现、或单纯等待指定秒数。"
        "三个参数至少指定一个。等待完成后自动刷新快照。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "等待出现的 CSS 选择器（可选）"},
            "text": {"type": "string", "description": "等待出现的文字内容（可选）"},
            "seconds": {"type": "number", "description": "等待秒数，1-30（可选，默认 3）"},
        },
    },
    risk_level="low",
)
async def browser_wait(selector: str = "", text: str = "", seconds: float = 3) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        waited_for = ""

        if selector:
            await browser._page.wait_for_selector(selector, timeout=15000)
            waited_for = f"元素 \"{selector}\" 已出现"
        elif text:
            await browser._page.wait_for_function(
                f'document.body.innerText.includes("{text.replace(chr(34), "")}")',
                timeout=15000,
            )
            waited_for = f"文字 \"{text}\" 已出现"
        else:
            seconds = max(0.5, min(seconds, 30))
            await asyncio.sleep(seconds)
            waited_for = f"已等待 {seconds} 秒"

        info, _ = await _SharedBrowser.do_snapshot()
        return SkillResult(success=True, data=f"{waited_for}\n\n{info}")
    except Exception as e:
        info, _ = await _SharedBrowser.do_snapshot()
        return SkillResult(success=False, error=f"等待超时: {e}\n\n当前页面快照:\n{info}")


@register(
    name="browser_rpa_config",
    description=(
        "配置 RPA 模式参数。RPA 模式模拟人类操作，帮助绕过反爬检测：\n"
        "- 鼠标平滑移动（贝塞尔曲线轨迹）\n"
        "- 人类化输入节奏（随机延迟）\n"
        "- 平滑滚动"
    ),
    parameters={
        "type": "object",
        "properties": {
            "enabled": {"type": "boolean", "description": "是否启用 RPA 模式（默认 true）"},
            "mouse_duration": {"type": "number", "description": "鼠标移动时长，秒（默认 0.2）"},
            "type_delay_min": {"type": "integer", "description": "输入最小延迟，毫秒（默认 30）"},
            "type_delay_max": {"type": "integer", "description": "输入最大延迟，毫秒（默认 80）"},
        },
    },
    risk_level="low",
)
async def browser_rpa_config(
    enabled: bool = None,
    mouse_duration: float = None,
    type_delay_min: int = None,
    type_delay_max: int = None,
) -> SkillResult:
    """配置 RPA 模式参数"""
    changes = []
    
    if enabled is not None:
        RPAConfig.enabled = enabled
        changes.append(f"RPA 模式: {'开启' if enabled else '关闭'}")
    
    if mouse_duration is not None:
        RPAConfig.mouse_move_duration = max(0.1, min(mouse_duration, 1.0))
        changes.append(f"鼠标移动时长: {RPAConfig.mouse_move_duration}s")
    
    if type_delay_min is not None:
        RPAConfig.type_delay_min = max(10, min(type_delay_min, 200))
        changes.append(f"输入最小延迟: {RPAConfig.type_delay_min}ms")
    
    if type_delay_max is not None:
        RPAConfig.type_delay_max = max(RPAConfig.type_delay_min, min(type_delay_max, 300))
        changes.append(f"输入最大延迟: {RPAConfig.type_delay_max}ms")
    
    if changes:
        return SkillResult(success=True, data="RPA 配置已更新:\n" + "\n".join(f"  ✓ {c}" for c in changes))
    
    # 没有参数时，显示当前配置
    config_info = (
        f"当前 RPA 配置（用于绕过反爬检测）:\n"
        f"  🖱️ RPA 模式: {'开启' if RPAConfig.enabled else '关闭'}\n"
        f"  🕐 鼠标移动时长: {RPAConfig.mouse_move_duration}s\n"
        f"  ⌨️ 输入延迟: {RPAConfig.type_delay_min}-{RPAConfig.type_delay_max}ms\n"
        f"  📜 平滑滚动: {'开启' if RPAConfig.scroll_smooth else '关闭'}"
    )
    return SkillResult(success=True, data=config_info)


@register(
    name="browser_history",
    description=(
        "查看当前浏览器会话的操作历史。用于：\n"
        "- 了解之前做过哪些操作\n"
        "- 避免重复执行相同的操作\n"
        "- 调试问题时查看操作序列"
    ),
    parameters={
        "type": "object",
        "properties": {
            "count": {"type": "integer", "description": "显示最近N条记录（默认10）"},
        },
    },
    risk_level="low",
)
async def browser_history(count: int = 10) -> SkillResult:
    """查看操作历史"""
    if not _SharedBrowser.is_active():
        return _not_active_error()
    
    actions = _SharedBrowser.get_recent_actions(count)
    if not actions:
        return SkillResult(success=True, data="暂无操作记录")
    
    lines = [f"最近 {len(actions)} 条操作记录:"]
    for i, action in enumerate(actions, 1):
        status = "✓" if action["success"] else "✗"
        timestamp = time.strftime("%H:%M:%S", time.localtime(action["timestamp"]))
        lines.append(f"  {i}. [{timestamp}] {status} {action['type']}({action['target'][:30]})")
        if not action["success"]:
            lines.append(f"      失败原因: {action['result'][:50]}")
    
    return SkillResult(success=True, data="\n".join(lines))


@register(
    name="browser_tabs",
    description="列出所有打开的标签页。可以配合 browser_tab_switch 切换标签页。",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
)
async def browser_tabs() -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        tabs = await browser.list_tabs()
        if not tabs:
            return SkillResult(success=True, data="没有打开的标签页")

        lines = [f"共 {len(tabs)} 个标签页:"]
        for t in tabs:
            marker = " ← 当前" if t["active"] else ""
            lines.append(f"  [{t['index']}] {t['title'][:50]} - {t['url'][:60]}{marker}")
        return SkillResult(success=True, data="\n".join(lines))
    except Exception as e:
        return SkillResult(success=False, error=f"获取标签页失败: {e}")


@register(
    name="browser_tab_switch",
    description="切换到指定编号的标签页。用 browser_tabs 查看标签页列表和编号。",
    parameters={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "标签页编号（从 0 开始）"},
        },
        "required": ["index"],
    },
    risk_level="low",
)
async def browser_tab_switch(index: int) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        result = await browser.switch_tab(index)
        if not result.success:
            return SkillResult(success=False, error=result.error)

        info, _ = await _SharedBrowser.do_snapshot()
        return SkillResult(success=True, data=f"已切换标签页\n\n{info}")
    except Exception as e:
        return SkillResult(success=False, error=f"切换标签页失败: {e}")


@register(
    name="browser_tab_new",
    description="打开一个新标签页，可选直接导航到 URL。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要打开的网址（可选，留空则打开空白页）"},
        },
    },
    risk_level="low",
)
async def browser_tab_new(url: str = "") -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        result = await browser.new_tab(url)
        if not result.success:
            return SkillResult(success=False, error=result.error)

        if url:
            await browser.wait_for_page_ready(timeout=12)
            info, _ = await _SharedBrowser.do_snapshot()
            return SkillResult(success=True, data=f"新标签页已打开\n\n{info}")
        return SkillResult(success=True, data="新标签页已打开（空白页）")
    except Exception as e:
        return SkillResult(success=False, error=f"新建标签页失败: {e}")


@register(
    name="browser_tab_close",
    description="关闭指定编号的标签页。不指定则关闭当前标签页。",
    parameters={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "标签页编号（不指定则关闭当前）"},
        },
    },
    risk_level="low",
)
async def browser_tab_close(index: int = -1) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        result = await browser.close_tab(index)
        return SkillResult(success=result.success, data=result.data, error=result.error)
    except Exception as e:
        return SkillResult(success=False, error=f"关闭标签页失败: {e}")


@register(
    name="browser_save_cookies",
    description="保存当前网站的 Cookie。登录成功后调用，以后访问该网站会自动加载登录状态。",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
)
async def browser_save_cookies() -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        count = await browser.save_current_cookies()
        if count:
            domain = urlparse(browser.get_current_url()).netloc
            return SkillResult(success=True, data=f"已保存 {domain} 的 {count} 个 Cookie")
        return SkillResult(success=False, error="当前没有 Cookie 可保存")
    except Exception as e:
        return SkillResult(success=False, error=f"保存 Cookie 失败: {e}")


@register(
    name="browser_screenshot_send",
    description="截图当前浏览器页面并发送到飞书。需要先用 browser_open 打开页面。",
    parameters={
        "type": "object",
        "properties": {
            "caption": {"type": "string", "description": "截图说明（可选）"},
            "full_page": {"type": "boolean", "description": "是否截取整页"},
        },
    },
    risk_level="low",
)
async def browser_screenshot_send(caption: str = "", full_page: bool = False) -> SkillResult:
    import os
    import uuid

    if not _SharedBrowser.is_active():
        return _not_active_error()
    if _feishu_channel is None:
        return SkillResult(success=False, error="飞书通道未初始化")

    chat_id = _feishu_channel.get_current_chat_id()
    if not chat_id:
        return SkillResult(success=False, error="没有活跃的飞书会话")

    temp_path = f"/tmp/screenshot_{uuid.uuid4().hex[:8]}.png"
    try:
        browser = await _SharedBrowser.get()
        # 截图前确保页面已渲染
        await browser.wait_for_page_ready(timeout=6)
        result = await browser.save_screenshot(temp_path, full_page)
        if not result.success:
            return SkillResult(success=False, error=f"截图失败: {result.error}")

        title = caption or f"页面截图: {browser.get_current_url()[:50]}"
        success = await _feishu_channel.send_image(chat_id, temp_path, title)

        if os.path.exists(temp_path):
            os.remove(temp_path)

        if success:
            return SkillResult(success=True, data="截图已发送到飞书")
        return SkillResult(success=False, error="图片发送失败")
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return SkillResult(success=False, error=f"截图发送失败: {e}")


@register(
    name="browser_screenshot_and_send",
    description="一次性打开网页截图并发送到飞书。适合简单截图需求（不保持浏览器状态）。",
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要截图的网址"},
            "caption": {"type": "string", "description": "图片说明（可选）"},
            "full_page": {"type": "boolean", "description": "是否截取整页"},
        },
        "required": ["url"],
    },
    risk_level="low",
)
async def browser_screenshot_and_send(url: str, caption: str = "", full_page: bool = False) -> SkillResult:
    import uuid

    if _feishu_channel is None:
        return SkillResult(success=False, error="飞书通道未初始化")

    chat_id = _feishu_channel.get_current_chat_id()
    if not chat_id:
        return SkillResult(success=False, error="没有活跃的飞书会话")

    temp_path = f"/tmp/screenshot_{uuid.uuid4().hex[:8]}.png"
    try:
        browser = await _SharedBrowser.get()
        result = await browser.goto(url)
        if not result.success:
            return SkillResult(success=False, error=f"打开网页失败: {result.error}")
        await browser.wait_for_page_ready(timeout=12)
        result = await browser.save_screenshot(temp_path, full_page)
        if not result.success:
            return SkillResult(success=False, error=f"截图失败: {result.error}")

        title = caption or f"网页截图: {url[:50]}"
        success = await _feishu_channel.send_image(chat_id, temp_path, title)

        if os.path.exists(temp_path):
            os.remove(temp_path)
        return SkillResult(success=True, data="截图已发送到飞书") if success else SkillResult(success=False, error="图片发送失败")
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        return SkillResult(success=False, error=f"截图发送失败: {e}")


@register(
    name="browser_get_text",
    description=(
        "获取当前页面或指定元素的文字内容。不传参获取整页摘要，传元素编号获取该元素内容。"
        "适合提取搜索结果、文章内容、表格数据等。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "元素编号（如 e3）或 CSS 选择器（默认 body）"},
        },
    },
    risk_level="low",
)
async def browser_get_text(ref: str = "body") -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        if ref == "body":
            text = await browser._page.inner_text("body")
        else:
            locator = await _smart_locate(browser._page, ref, _SharedBrowser._ref_map, "text")
            text = await locator.inner_text(timeout=5000)
        return SkillResult(success=True, data=text[:5000])
    except Exception as e:
        return SkillResult(success=False, error=f"获取文字失败: {e}")


@register(
    name="browser_execute_js",
    description=(
        "在当前页面执行 JavaScript 代码并返回结果。\n"
        "⚠️ 重要限制：每次调用都是独立的 JavaScript 执行上下文！\n"
        "   - 上一次调用中定义的变量、函数在下一次调用中不存在\n"
        "   - 不要试图跨多次调用传递数据\n"
        "   - 所有逻辑必须在一次调用中完成\n"
        "   - 如果需要多步操作，请合并成一段完整的 JS 代码\n"
        "适用场景：提取页面数据、执行复杂 DOM 操作、获取页面信息。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 JavaScript 代码（必须是完整自包含的，不能依赖之前调用的变量）"},
        },
        "required": ["code"],
    },
    risk_level="medium",
)
async def browser_execute_js(code: str) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        result = await browser.evaluate(code)
        result_str = str(result.data)[:5000]
        return SkillResult(
            success=True,
            data=f"{result_str}\n\n[提示: 每次 JS 执行都是独立上下文，变量不会保留到下次调用]"
        )
    except Exception as e:
        return SkillResult(
            success=False,
            error=f"JS 执行失败: {e}\n[提示: 每次调用是独立上下文，确保代码是自包含的，不依赖之前定义的变量]"
        )


@register(
    name="browser_select",
    description=(
        "操作下拉框的专用工具（原生 <select> 和自定义下拉组件都支持，iframe 内也能用）。\n"
        "⚠️ 遇到下拉框（combobox/select）必须用这个工具，不要用 browser_click 或 browser_execute_js。\n"
        "自动处理：展开下拉 → 在列表中滚动查找 → 点击选中。\n"
        "支持模糊匹配（如 value='中国大陆' 能匹配选项 '+86 中国大陆'）。\n"
        "直接用这个工具，不需要先 snapshot 或 analyze_page。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "下拉框元素编号（如 e3）或 CSS 选择器"},
            "value": {"type": "string", "description": "要选择的选项文本或值（支持模糊匹配）"},
            "index": {"type": "integer", "description": "按索引选择（从0开始），与 value 二选一"},
        },
        "required": ["ref"],
    },
    risk_level="low",
)
async def browser_select(ref: str, value: str = None, index: int = None) -> SkillResult:
    """下拉框选择 - 支持 iframe、原生/自定义下拉、长列表滚动、模糊匹配"""
    if not _SharedBrowser.is_active():
        return _not_active_error()
    if value is None and index is None:
        return SkillResult(success=False, error="必须提供 value 或 index 参数")

    try:
        browser = await _SharedBrowser.get()
        locator = await _smart_locate(browser._page, ref, _SharedBrowser._ref_map, "select")

        # 确定正确的 frame（可能是 iframe 内的元素）
        ref_info = _SharedBrowser._ref_map.get(ref, {})
        target_frame = _find_frame_for_ref(browser._page, ref_info)

        # 策略1: 原生 <select>
        try:
            tag = await locator.evaluate("el => el.tagName.toLowerCase()")
            if tag == "select":
                if value is not None:
                    # 先尝试模糊匹配 label
                    try:
                        await locator.select_option(label=value, timeout=3000)
                    except Exception:
                        # 再尝试精确 value
                        try:
                            await locator.select_option(value=value, timeout=3000)
                        except Exception:
                            # 最后用 JS 做包含匹配
                            matched = await locator.evaluate(
                                """(el, target) => {
                                    for (const opt of el.options) {
                                        if (opt.text.includes(target) || opt.value.includes(target)) {
                                            el.value = opt.value;
                                            el.dispatchEvent(new Event('change', {bubbles: true}));
                                            return opt.text;
                                        }
                                    }
                                    return null;
                                }""", value
                            )
                            if not matched:
                                info, _ = await _SharedBrowser.do_snapshot()
                                return SkillResult(success=False, error=f"原生 select 中未找到包含 \"{value}\" 的选项\n\n{info}")
                else:
                    await locator.select_option(index=index, timeout=3000)

                info, _ = await _SharedBrowser.do_snapshot()
                _SharedBrowser.record_action("select", ref, f"已选择: {value or index}", True)
                return SkillResult(success=True, data=f"✓ 已选择: {value or f'索引{index}'}\n\n{info}")
        except Exception:
            pass

        # 策略2: 自定义下拉框 — 点击展开
        if RPAConfig.enabled:
            await browser.rpa_move_mouse_to(locator)
        await locator.click(timeout=5000)
        await asyncio.sleep(0.5)

        # 搜索选项时，在所有相关 frame 中查找（主页面 + iframe）
        search_frames = [target_frame]
        if target_frame != browser._page:
            search_frames.append(browser._page)

        if value is not None:
            option_locator = None

            def _build_search_strategies(frame):
                """在指定 frame 上构建搜索策略（精确 + 模糊）"""
                safe_val = value.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")
                return [
                    lambda: frame.get_by_role("option", name=value).first,
                    lambda: frame.locator(f'[role="listbox"] >> text="{safe_val}"').first,
                    lambda: frame.locator(
                        f'.ant-select-dropdown >> text="{safe_val}", '
                        f'.el-select-dropdown >> text="{safe_val}", '
                        f'.dropdown-menu >> text="{safe_val}", '
                        f'[class*="dropdown"] >> text="{safe_val}", '
                        f'[class*="select-dropdown"] >> text="{safe_val}", '
                        f'[class*="listbox"] >> text="{safe_val}"'
                    ).first,
                    lambda: frame.get_by_text(value, exact=True).first,
                    lambda: frame.get_by_text(value, exact=False).first,
                ]

            # 第一轮：在所有 frame 的可见区域查找
            for frame in search_frames:
                for strategy in _build_search_strategies(frame):
                    try:
                        candidate = strategy()
                        if await candidate.count() > 0:
                            try:
                                await candidate.scroll_into_view_if_needed(timeout=2000)
                            except Exception:
                                pass
                            option_locator = candidate
                            break
                    except Exception:
                        continue
                if option_locator:
                    break

            # 第二轮：在下拉面板内逐步滚动查找
            if option_locator is None:
                logger.info(f"选项 '{value}' 在可见区域未找到，尝试滚动查找...")
                _scroll_js = """async (args) => {
                    const [targetText, maxScrolls] = args;
                    const panels = document.querySelectorAll(
                        '[role="listbox"], .ant-select-dropdown, .el-select-dropdown__wrap, '
                        + '.el-scrollbar__wrap, .dropdown-menu, [class*="select-dropdown"], '
                        + '[class*="dropdown-list"], [class*="options-list"], [class*="listbox"], '
                        + 'ul, ol'
                    );
                    let scrollPanel = null;
                    for (const p of panels) {
                        const style = window.getComputedStyle(p);
                        if (style.display !== 'none' && style.visibility !== 'hidden' && p.offsetHeight > 0) {
                            if (p.scrollHeight > p.clientHeight + 5) {
                                scrollPanel = p;
                                break;
                            }
                        }
                    }
                    if (!scrollPanel) return { found: false, reason: 'no_scrollable_panel' };
                    for (let i = 0; i < maxScrolls; i++) {
                        const items = scrollPanel.querySelectorAll(
                            '[role="option"], .ant-select-item, .el-select-dropdown__item, '
                            + '[class*="option"], li, [class*="item"], a, span, div'
                        );
                        for (const item of items) {
                            const text = (item.textContent || '').trim();
                            if (text === targetText || text.includes(targetText)) {
                                item.scrollIntoView({ block: 'center', behavior: 'smooth' });
                                return { found: true, text: text };
                            }
                        }
                        scrollPanel.scrollTop += scrollPanel.clientHeight * 0.8;
                        await new Promise(r => setTimeout(r, 200));
                    }
                    return { found: false, reason: 'not_found_after_scroll' };
                }"""

                for frame in search_frames:
                    try:
                        scroll_result = await frame.evaluate(_scroll_js, [value, 15])
                        if scroll_result and scroll_result.get("found"):
                            await asyncio.sleep(0.2)
                            for strategy in _build_search_strategies(frame):
                                try:
                                    candidate = strategy()
                                    if await candidate.count() > 0:
                                        option_locator = candidate
                                        break
                                except Exception:
                                    continue
                            if option_locator:
                                break
                    except Exception as e:
                        logger.debug(f"在 frame 中滚动查找失败: {e}")

            # 第三轮：键盘搜索
            if option_locator is None:
                try:
                    await target_frame.evaluate("() => document.activeElement && document.activeElement.focus()")
                    await browser._page.keyboard.type(value, delay=50)
                    await asyncio.sleep(0.5)

                    for frame in search_frames:
                        for strategy in _build_search_strategies(frame)[:3]:
                            try:
                                candidate = strategy()
                                if await candidate.count() > 0:
                                    option_locator = candidate
                                    break
                            except Exception:
                                continue
                        if option_locator:
                            break

                    if option_locator is None:
                        await browser._page.keyboard.press("Escape")
                        await asyncio.sleep(0.3)
                        info, _ = await _SharedBrowser.do_snapshot()
                        _SharedBrowser.record_action("select", ref, f"键盘搜索未找到: {value}", False)
                        return SkillResult(
                            success=False,
                            error=f"键盘搜索后仍未找到选项 \"{value}\"。请检查快照确认可选项:\n\n{info}"
                        )
                except Exception:
                    info, _ = await _SharedBrowser.do_snapshot()
                    return SkillResult(
                        success=False,
                        error=f"找不到选项 \"{value}\"。下拉框已展开，当前可见选项请查看快照:\n\n{info}"
                    )

            # 点击找到的选项
            if RPAConfig.enabled:
                await browser.rpa_move_mouse_to(option_locator)
            await option_locator.click(timeout=5000)

        elif index is not None:
            # 按索引选择 — 在正确的 frame 中查找
            options = target_frame.locator(
                '[role="option"], [role="listbox"] > *, '
                '.ant-select-item, .el-select-dropdown__item, [class*="option"]'
            )
            count = await options.count()
            if count == 0 and target_frame != browser._page:
                options = browser._page.locator(
                    '[role="option"], [role="listbox"] > *, '
                    '.ant-select-item, .el-select-dropdown__item, [class*="option"]'
                )
                count = await options.count()
            if index >= count:
                return SkillResult(success=False, error=f"索引 {index} 超出范围，共 {count} 个选项")
            target = options.nth(index)
            try:
                await target.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            if RPAConfig.enabled:
                await browser.rpa_move_mouse_to(target)
            await target.click(timeout=5000)

        await asyncio.sleep(0.3)
        info, _ = await _SharedBrowser.do_snapshot()
        _SharedBrowser.record_action("select", ref, f"已选择: {value or index}", True)
        return SkillResult(success=True, data=f"✓ 已选择: {value or f'索引{index}'}\n\n{info}")

    except Exception as e:
        _SharedBrowser.record_action("select", ref, str(e)[:80], False)
        return SkillResult(success=False, error=f"下拉框操作失败: {e}")


@register(
    name="browser_scroll_element",
    description=(
        "在指定容器元素内滚动（下拉面板、侧栏、可滚动区域等）。\n"
        "不同于 browser_scroll_page 只能滚动整个页面，这个工具可以在特定的可滚动容器内滚动。\n"
        "典型用途：下拉框展开后在选项列表中滚动查找、长列表滚动加载更多。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": "可滚动容器的元素编号或 CSS 选择器（如 e5、.list-container、[role='listbox']）"
            },
            "direction": {
                "type": "string",
                "enum": ["down", "up", "top", "bottom"],
                "description": "滚动方向，默认 down"
            },
            "amount": {
                "type": "integer",
                "description": "滚动距离(px)，默认300。设为0表示滚动一屏"
            },
        },
        "required": ["ref"],
    },
    risk_level="low",
)
async def browser_scroll_element(ref: str, direction: str = "down", amount: int = 300) -> SkillResult:
    """在指定容器内滚动"""
    if not _SharedBrowser.is_active():
        return _not_active_error()
    
    try:
        browser = await _SharedBrowser.get()
        locator = await _smart_locate(browser._page, ref, _SharedBrowser._ref_map, "scroll_element")
        
        scroll_js = """
        (el, args) => {
            const [direction, amount] = args;
            const h = amount || Math.floor(el.clientHeight * 0.8);
            const map = {
                'down': () => el.scrollTop += h,
                'up': () => el.scrollTop -= h,
                'top': () => el.scrollTop = 0,
                'bottom': () => el.scrollTop = el.scrollHeight,
            };
            const before = el.scrollTop;
            (map[direction] || map['down'])();
            const after = el.scrollTop;
            return {
                scrolled: Math.abs(after - before),
                scrollTop: after,
                scrollHeight: el.scrollHeight,
                clientHeight: el.clientHeight,
                atTop: after <= 0,
                atBottom: after + el.clientHeight >= el.scrollHeight - 2
            };
        }
        """
        
        result = await locator.evaluate(scroll_js, [direction, amount])
        await asyncio.sleep(0.3)
        
        info, _ = await _SharedBrowser.do_snapshot()
        
        pos_desc = ""
        if result.get("atTop"):
            pos_desc = "（已到顶部）"
        elif result.get("atBottom"):
            pos_desc = "（已到底部）"
        else:
            pct = int(result.get("scrollTop", 0) / max(result.get("scrollHeight", 1) - result.get("clientHeight", 0), 1) * 100)
            pos_desc = f"（位置 {pct}%）"
        
        return SkillResult(
            success=True,
            data=f"✓ 容器已{direction}滚动 {result.get('scrolled', 0)}px {pos_desc}\n\n{info}"
        )
    
    except Exception as e:
        return SkillResult(success=False, error=f"容器滚动失败: {e}")


@register(
    name="browser_hover",
    description=(
        "鼠标悬停在元素上。用于：\n"
        "- 触发悬停菜单、子菜单\n"
        "- 显示 tooltip 提示信息\n"
        "- 触发下拉展开效果\n"
        "悬停后自动刷新快照，可以看到新出现的元素。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "要悬停的元素编号（如 e1）或 CSS 选择器"},
        },
        "required": ["ref"],
    },
    risk_level="low",
)
async def browser_hover(ref: str) -> SkillResult:
    """鼠标悬停"""
    if not _SharedBrowser.is_active():
        return _not_active_error()

    try:
        browser = await _SharedBrowser.get()
        locator = await _smart_locate(browser._page, ref, _SharedBrowser._ref_map, "hover")

        if RPAConfig.enabled:
            await browser.rpa_move_mouse_to(locator)
        else:
            await locator.hover(timeout=5000)

        await asyncio.sleep(0.5)
        info, _ = await _SharedBrowser.do_snapshot()
        _SharedBrowser.record_action("hover", ref, "悬停成功", True)
        return SkillResult(success=True, data=f"✓ 已悬停在 {ref}\n\n{info}")

    except Exception as e:
        return SkillResult(success=False, error=f"悬停失败: {e}")


@register(
    name="browser_drag",
    description=(
        "拖拽元素。用于：\n"
        "- ⚠️ 滑块验证码（拖动滑块到指定位置）\n"
        "- 拖动滑杆、进度条\n"
        "- 拖放操作\n"
        "支持两种模式：\n"
        "1. 指定像素偏移: browser_drag(ref='e5', x_offset=280) — 向右拖280px\n"
        "2. 拖到另一个元素: browser_drag(ref='e5', target_ref='e8')\n"
        "拖拽轨迹模拟人类操作（贝塞尔曲线 + 随机抖动），可通过滑块验证。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "要拖拽的元素（滑块按钮）编号或选择器"},
            "x_offset": {"type": "integer", "description": "水平拖拽距离(px)，正值向右，负值向左"},
            "y_offset": {"type": "integer", "description": "垂直拖拽距离(px)，正值向下（默认0）"},
            "target_ref": {"type": "string", "description": "拖拽目标元素（拖到这个元素上），与 offset 二选一"},
            "speed": {"type": "string", "enum": ["slow", "normal", "fast"], "description": "拖拽速度（默认 normal）"},
        },
        "required": ["ref"],
    },
    risk_level="low",
)
async def browser_drag(
    ref: str,
    x_offset: int = 0,
    y_offset: int = 0,
    target_ref: str = None,
    speed: str = "normal",
) -> SkillResult:
    """拖拽元素 - 支持滑块验证码、拖放操作，模拟人类轨迹"""
    if not _SharedBrowser.is_active():
        return _not_active_error()
    if x_offset == 0 and y_offset == 0 and target_ref is None:
        return SkillResult(success=False, error="请指定拖拽距离 (x_offset/y_offset) 或目标元素 (target_ref)")

    try:
        return await asyncio.wait_for(
            _browser_drag_impl(ref, x_offset, y_offset, target_ref, speed), timeout=30
        )
    except asyncio.TimeoutError:
        return SkillResult(success=False, error="拖拽操作超时（30s）")
    except Exception as e:
        _SharedBrowser.record_action("drag", ref, str(e)[:80], False)
        return SkillResult(success=False, error=f"拖拽失败: {e}")


async def _browser_drag_impl(ref, x_offset, y_offset, target_ref, speed) -> SkillResult:
    """拖拽核心实现"""
    try:
        browser = await _SharedBrowser.get()
        page = browser._page
        locator = await _smart_locate(page, ref, _SharedBrowser._ref_map, "drag")

        box = await locator.bounding_box()
        if not box:
            return SkillResult(success=False, error=f"元素 {ref} 不可见或无法获取位置")

        start_x = box["x"] + box["width"] / 2
        start_y = box["y"] + box["height"] / 2

        if target_ref:
            target_locator = await _smart_locate(page, target_ref, _SharedBrowser._ref_map, "drag_target")
            target_box = await target_locator.bounding_box()
            if not target_box:
                return SkillResult(success=False, error=f"目标元素 {target_ref} 不可见")
            end_x = target_box["x"] + target_box["width"] / 2
            end_y = target_box["y"] + target_box["height"] / 2
        else:
            end_x = start_x + x_offset
            end_y = start_y + y_offset

        speed_config = {
            "slow": {"steps": 40, "step_delay": 0.02, "pause": 0.3},
            "normal": {"steps": 25, "step_delay": 0.012, "pause": 0.15},
            "fast": {"steps": 15, "step_delay": 0.006, "pause": 0.05},
        }
        cfg = speed_config.get(speed, speed_config["normal"])

        path = _generate_mouse_path(start_x, start_y, end_x, end_y, cfg["steps"])

        jittered_path = []
        for i, (px, py) in enumerate(path):
            progress = i / max(len(path) - 1, 1)
            if 0.1 < progress < 0.9:
                jitter_y = random.uniform(-2, 2)
                jitter_x = random.uniform(-0.5, 0.5)
            else:
                jitter_y = 0
                jitter_x = 0
            jittered_path.append((px + jitter_x, py + jitter_y))

        # 修正最后几步：逐渐靠近目标（模拟减速）
        for i in range(min(3, len(jittered_path))):
            idx = len(jittered_path) - 1 - i
            weight = (i + 1) / 4.0
            jittered_path[idx] = (
                jittered_path[idx][0] * (1 - weight) + end_x * weight,
                jittered_path[idx][1] * (1 - weight) + end_y * weight,
            )

        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(0.1)
        await page.mouse.down()
        await asyncio.sleep(cfg["pause"])

        for px, py in jittered_path:
            await page.mouse.move(px, py)
            delay = cfg["step_delay"] * random.uniform(0.7, 1.5)
            await asyncio.sleep(delay)

        await page.mouse.move(end_x, end_y)
        await asyncio.sleep(cfg["pause"])
        await page.mouse.up()
        await asyncio.sleep(0.5)

        info, _ = await _SharedBrowser.do_snapshot()
        dist = f"{int(end_x - start_x)}px, {int(end_y - start_y)}px"
        _SharedBrowser.record_action("drag", ref, f"拖拽 {dist}", True)
        return SkillResult(
            success=True,
            data=f"✓ 已拖拽 {ref} ({dist})\n\n{info}"
        )

    except Exception as e:
        raise


_SLIDER_FIND_JS = """
() => {
    // 在当前 frame 中查找滑块验证码
    // 常见滑块选择器（阿里系、腾讯系、极验等）
    const sliderSelectors = [
        '#nc_1_n1z',                          // 阿里 noCAPTCHA
        '.nc_iconfont.btn_slide',             // 阿里滑块按钮
        '.nc-lang-cnt .btn_slide',            // 阿里滑块
        '#nocaptcha .nc_scale .btn_slide',    // 阿里 noCAPTCHA v2
        '.nc_scale .btn_slide',               // 阿里
        '.slider-btn',                        // 通用
        '.slide-btn',                         // 通用
        '.geetest_slider_button',             // 极验
        '.gt_slider_knob',                    // 极验 v3
        '#tcaptcha_drag_button',              // 腾讯
        '.tc-fg-item',                        // 腾讯
        '[class*="slider"] [class*="btn"]',   // 通用模式
        '[class*="slider"] [class*="button"]',
        '[class*="captcha"] [class*="slider"]',
        '[class*="slide"] [class*="handle"]',
        '[class*="drag"] [class*="btn"]',
        'input[type="range"]',                // range slider
    ];
    
    // 滑道选择器
    const trackSelectors = [
        '#nc_1__scale_text',                  // 阿里滑道
        '.nc_scale',                          // 阿里滑道
        '.nc-lang-cnt',                       // 阿里
        '.slider-track',                      // 通用
        '.slide-track',                       // 通用
        '.geetest_slider',                    // 极验
        '#tcaptcha_drag_thumb',               // 腾讯
        '[class*="slider"][class*="track"]',
        '[class*="slider"][class*="bar"]',
        '[class*="slide"][class*="track"]',
        '[class*="captcha"][class*="track"]',
    ];
    
    let slider = null;
    let sliderSel = '';
    for (const sel of sliderSelectors) {
        const el = document.querySelector(sel);
        if (el && el.offsetWidth > 0 && el.offsetHeight > 0) {
            slider = el;
            sliderSel = sel;
            break;
        }
    }
    
    // 没找到就试文本匹配
    if (!slider) {
        const allEls = document.querySelectorAll('span, div, button, a');
        for (const el of allEls) {
            const text = (el.textContent || '').trim();
            const style = window.getComputedStyle(el);
            if ((text.includes('滑动') || text.includes('拖动') || text.includes('slide') || text.includes('drag'))
                && el.offsetWidth > 20 && el.offsetWidth < 80
                && el.offsetHeight > 15 && el.offsetHeight < 60
                && style.cursor && (style.cursor.includes('pointer') || style.cursor.includes('move') || style.cursor.includes('grab'))) {
                slider = el;
                sliderSel = 'text_match';
                break;
            }
        }
    }
    
    if (!slider) return null;
    
    const sliderRect = slider.getBoundingClientRect();
    
    // 找滑道来确定拖动距离
    let trackWidth = 300; // 默认值
    for (const sel of trackSelectors) {
        const track = document.querySelector(sel);
        if (track && track.offsetWidth > 50) {
            trackWidth = track.getBoundingClientRect().width;
            break;
        }
    }
    
    // 如果没找到滑道，用滑块的父元素宽度
    if (trackWidth <= 50) {
        let parent = slider.parentElement;
        for (let i = 0; i < 5 && parent; i++) {
            if (parent.offsetWidth > 100) {
                trackWidth = parent.getBoundingClientRect().width;
                break;
            }
            parent = parent.parentElement;
        }
    }
    
    return {
        found: true,
        selector: sliderSel,
        x: sliderRect.x + sliderRect.width / 2,
        y: sliderRect.y + sliderRect.height / 2,
        width: sliderRect.width,
        height: sliderRect.height,
        trackWidth: trackWidth,
        dragDistance: trackWidth - sliderRect.width - 5,
    };
}
"""


@register(
    name="browser_solve_slider",
    description=(
        "⚠️ 自动完成滑块验证码。不需要提供元素编号。\n"
        "自动在所有 iframe 中搜索滑块 → 计算拖动距离 → 用人类化轨迹拖动完成验证。\n"
        "支持阿里系(闲鱼/淘宝)、极验、腾讯等常见滑块验证码。\n"
        "遇到滑块验证码时直接调用这个工具，不需要先分析页面。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "distance": {
                "type": "integer",
                "description": "手动指定拖动距离(px)，不填则自动计算"
            },
        },
    },
    risk_level="low",
)
async def browser_solve_slider(distance: int = None) -> SkillResult:
    """自动搜索并完成滑块验证码（使用 locator 自动处理 iframe 坐标）"""
    if not _SharedBrowser.is_active():
        return _not_active_error()

    try:
        return await asyncio.wait_for(
            _browser_solve_slider_impl(distance), timeout=30
        )
    except asyncio.TimeoutError:
        return SkillResult(success=False, error="滑块验证超时（30s），可能页面未加载完成或滑块已失效")
    except Exception as e:
        _SharedBrowser.record_action("solve_slider", "auto", str(e)[:80], False)
        return SkillResult(success=False, error=f"滑块验证失败: {e}")


async def _browser_solve_slider_impl(distance: int = None) -> SkillResult:
    """滑块验证的核心实现（带超时保护的外壳在 browser_solve_slider 中）"""
    browser = await _SharedBrowser.get()
    page = browser._page

    slider_info = None
    target_frame = None

    frames = page.frames[:20]
    for frame in frames:
        try:
            frame_url = getattr(frame, 'url', '') or ''
            result = await asyncio.wait_for(frame.evaluate(_SLIDER_FIND_JS), timeout=5)
            if result and result.get("found"):
                slider_info = result
                target_frame = frame
                logger.info(f"在 frame {frame_url[:60]} 中找到滑块: {result.get('selector')}")
                break
        except Exception as e:
            logger.debug(f"搜索滑块失败: {e}")
            continue

    if not slider_info:
        info, _ = await _SharedBrowser.do_snapshot()
        return SkillResult(
            success=False,
            error=f"在所有 frame 中未找到滑块验证码。可能：\n"
                  f"1. 滑块是图片拼图类型，需要用 browser_drag 手动指定偏移\n"
                  f"2. 滑块尚未加载，请稍等后重试\n\n{info}"
        )

    sel = slider_info.get("selector", "")
    slider_locator = None
    slider_box = None

    if sel and sel != "text_match":
        try:
            slider_locator = target_frame.locator(sel).first
            slider_box = await slider_locator.bounding_box()
        except Exception:
            pass

    if not slider_box:
        iframe_offset_x, iframe_offset_y = 0, 0
        try:
            current = target_frame
            while current and current != page.main_frame:
                fe = await current.frame_element()
                if fe:
                    b = await fe.bounding_box()
                    if b:
                        iframe_offset_x += b["x"]
                        iframe_offset_y += b["y"]
                current = current.parent_frame
        except Exception:
            pass
        slider_box = {
            "x": slider_info["x"] - slider_info["width"] / 2 + iframe_offset_x,
            "y": slider_info["y"] - slider_info["height"] / 2 + iframe_offset_y,
            "width": slider_info["width"],
            "height": slider_info["height"],
        }

    start_x = slider_box["x"] + slider_box["width"] / 2
    start_y = slider_box["y"] + slider_box["height"] / 2

    drag_dist = distance if distance else int(slider_info.get("dragDistance", 260))
    if drag_dist < 50:
        drag_dist = 260

    end_x = start_x + drag_dist
    end_y = start_y

    logger.info(f"滑块主页面坐标: ({start_x:.0f}, {start_y:.0f}), 拖动距离: {drag_dist}px")

    await page.mouse.move(start_x, start_y - 30)
    await asyncio.sleep(random.uniform(0.2, 0.4))
    await page.mouse.move(start_x, start_y)
    await asyncio.sleep(random.uniform(0.15, 0.3))

    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.1, 0.2))

    total_steps = random.randint(28, 40)
    current_x = start_x
    current_y = start_y

    for i in range(total_steps):
        progress = (i + 1) / total_steps
        eased = 1 - (1 - progress) ** 2.5

        target_x = start_x + drag_dist * eased
        target_y = start_y

        if 0.1 < progress < 0.8:
            jitter_y = random.uniform(-3, 3)
        else:
            jitter_y = random.uniform(-0.5, 0.5)

        current_x = target_x + random.uniform(-0.5, 0.5)
        current_y = target_y + jitter_y

        await page.mouse.move(current_x, current_y)

        if progress < 0.2:
            delay = random.uniform(0.008, 0.015)
        elif progress < 0.7:
            delay = random.uniform(0.012, 0.025)
        else:
            delay = random.uniform(0.02, 0.045)
        await asyncio.sleep(delay)

    await page.mouse.move(end_x, end_y)
    await asyncio.sleep(random.uniform(0.05, 0.15))

    await page.mouse.move(end_x - random.uniform(1, 3), end_y + random.uniform(-1, 1))
    await asyncio.sleep(random.uniform(0.02, 0.05))
    await page.mouse.move(end_x, end_y)
    await asyncio.sleep(random.uniform(0.08, 0.15))

    await page.mouse.up()

    await asyncio.sleep(2.5)

    passed = False
    try:
        check_result = await target_frame.evaluate("""
        () => {
            const okEl = document.querySelector('.nc-lang-cnt .nc_ok, .nc_ok_text, [data-nc-result="ok"]');
            if (okEl) return { passed: true, type: 'ali_ok' };
            const slider = document.querySelector('#nc_1_n1z, .btn_slide');
            if (slider && slider.offsetWidth === 0) return { passed: true, type: 'slider_gone' };
            const scale = document.querySelector('.nc_scale');
            if (scale) {
                const style = window.getComputedStyle(scale);
                if (style.backgroundColor && (style.backgroundColor.includes('green') || style.backgroundColor.includes('76, 175') || style.backgroundColor.includes('67, 160')))
                    return { passed: true, type: 'turned_green' };
            }
            return { passed: false };
        }
        """)
        if check_result and check_result.get("passed"):
            passed = True
            logger.info(f"滑块验证通过！检测方式: {check_result.get('type')}")
    except Exception:
        pass

    info, _ = await _SharedBrowser.do_snapshot()
    _SharedBrowser.record_action("solve_slider", "auto", f"拖动 {drag_dist}px, 通过={passed}", True)

    if passed:
        return SkillResult(
            success=True,
            data=f"✓ 滑块验证已通过！（拖动 {drag_dist}px）\n\n{info}"
        )
    else:
        return SkillResult(
            success=True,
            data=f"⚠️ 滑块已拖动 {drag_dist}px，但可能未通过验证。\n"
                 f"可能原因：反自动化检测、距离不对。可以重试或让用户手动完成。\n\n{info}"
        )


@register(
    name="browser_press_key",
    description=(
        "按下键盘按键。用于：\n"
        "- Escape: 关闭弹窗、下拉框\n"
        "- Enter: 提交表单、确认选择\n"
        "- Tab: 切换到下一个输入框\n"
        "- ArrowDown/ArrowUp: 在下拉框中上下选择\n"
        "- Backspace: 删除输入内容\n"
        "- 组合键如 Control+a（全选）、Control+c（复制）"
    ),
    parameters={
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "按键名称，如: Enter, Escape, Tab, ArrowDown, ArrowUp, Backspace, Control+a"
            },
            "ref": {
                "type": "string",
                "description": "在哪个元素上按键（可选，不填则在当前焦点元素上按）"
            },
        },
        "required": ["key"],
    },
    risk_level="low",
)
async def browser_press_key(key: str, ref: str = None) -> SkillResult:
    """键盘按键"""
    if not _SharedBrowser.is_active():
        return _not_active_error()

    try:
        browser = await _SharedBrowser.get()

        if ref:
            locator = await _smart_locate(browser._page, ref, _SharedBrowser._ref_map, "press")
            await locator.press(key, timeout=5000)
        else:
            await browser._page.keyboard.press(key)

        await asyncio.sleep(0.3)
        await browser.wait_for_page_ready(timeout=3)

        info, _ = await _SharedBrowser.do_snapshot()
        _SharedBrowser.record_action("press_key", key, "按键成功", True)
        return SkillResult(success=True, data=f"✓ 已按下 {key}\n\n{info}")

    except Exception as e:
        return SkillResult(success=False, error=f"按键失败: {e}")


@register(
    name="browser_close",
    description="关闭浏览器释放资源。浏览器空闲 5 分钟也会自动关闭，通常不需要手动调用。",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
)
async def browser_close_session() -> SkillResult:
    if not _SharedBrowser.is_active():
        return SkillResult(success=True, data="浏览器已经是关闭状态")
    await _SharedBrowser.close()
    return SkillResult(success=True, data="浏览器已关闭，资源已释放")


# ==================== 网络请求监听 ====================


@register(
    name="browser_network_start",
    description=(
        "开始监听浏览器的网络请求。"
        "电商网站（淘宝/闲鱼/京东等）的商品数据通常通过 XHR/API 接口返回 JSON，"
        "监听网络可以直接拿到结构化数据，比解析 HTML 更准确高效。"
        "建议：先开启监听 → 再操作页面（搜索/翻页/滚动）→ 最后用 browser_network_get 查看捕获的数据。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url_filter": {
                "type": "string",
                "description": "只捕获 URL 包含此关键词的请求（如 'api' 'search' 'mtop'），留空捕获全部",
            },
        },
    },
    risk_level="low",
)
async def browser_network_start(url_filter: str = "") -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        await _SharedBrowser.start_network_monitor(url_filter)
        msg = "网络监听已开启"
        if url_filter:
            msg += f"，过滤: URL 包含 \"{url_filter}\""
        msg += "\n现在操作页面（搜索、翻页、滚动），然后用 browser_network_get 查看捕获的请求。"
        return SkillResult(success=True, data=msg)
    except Exception as e:
        return SkillResult(success=False, error=f"开启监听失败: {e}")


@register(
    name="browser_network_get",
    description=(
        "获取已捕获的网络请求数据。可以按 URL 关键词和内容类型筛选。"
        "返回请求的 URL、状态码、内容类型和响应体预览（JSON 数据等）。"
        "对电商场景特别有用：搜索商品后可直接拿到 API 返回的 JSON 商品列表。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "url_contains": {
                "type": "string",
                "description": "筛选 URL 包含此关键词的请求（如 'search' 'item' 'product'）",
            },
            "content_type": {
                "type": "string",
                "description": "筛选内容类型（如 'json' 'html'）",
            },
            "limit": {
                "type": "integer",
                "description": "返回最近 N 条（默认 20）",
            },
        },
    },
    risk_level="low",
)
async def browser_network_get(url_contains: str = "", content_type: str = "",
                               limit: int = 20) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()

    entries = _SharedBrowser.get_network_log(url_contains, content_type, limit)
    if not entries:
        return SkillResult(success=True, data="未捕获到匹配的网络请求。确保已开启监听且已操作页面。")

    lines = [f"捕获到 {len(entries)} 条网络请求（共 {len(_SharedBrowser._network_log)} 条）:\n"]
    for i, e in enumerate(entries, 1):
        lines.append(f"--- 请求 {i} ---")
        lines.append(f"  {e['method']} {e['url']}")
        lines.append(f"  状态: {e['status']} | 类型: {e['content_type']}")
        if e.get("body_preview"):
            preview = e["body_preview"]
            if len(preview) > 1500:
                preview = preview[:1500] + "... [截断]"
            lines.append(f"  响应:\n{preview}")
        lines.append("")

    result = "\n".join(lines)
    if len(result) > 12000:
        result = result[:12000] + "\n... [输出过长已截断，请用 url_contains 或 content_type 缩小范围]"

    return SkillResult(success=True, data=result)


@register(
    name="browser_network_stop",
    description="停止网络监听，释放内存。",
    parameters={"type": "object", "properties": {}},
    risk_level="low",
)
async def browser_network_stop() -> SkillResult:
    _SharedBrowser.stop_network_monitor()
    count = len(_SharedBrowser._network_log)
    return SkillResult(success=True, data=f"网络监听已停止，共捕获 {count} 条请求")


# ==================== 结构化数据提取 ====================


_JS_EXTRACT_BY_SELECTOR = """
(args) => {
    const {selector, attrs, maxCount} = args;
    const elements = document.querySelectorAll(selector);
    const results = [];
    for (let i = 0; i < Math.min(elements.length, maxCount); i++) {
        const el = elements[i];
        const item = {};
        item._text = (el.innerText || '').trim().slice(0, 200);
        item._tag = el.tagName.toLowerCase();
        if (el.href) item._href = el.href;
        if (el.src) item._src = el.src;
        for (const attr of attrs) {
            const val = el.getAttribute(attr);
            if (val) item[attr] = val.slice(0, 200);
        }
        results.push(item);
    }
    return results;
}
"""


_JS_EXTRACT_BY_XPATH = """
(args) => {
    const {xpath, attrs, maxCount} = args;
    const results = [];
    const snapshot = document.evaluate(xpath, document, null,
        XPathResult.ORDERED_NODE_SNAPSHOT_TYPE, null);
    for (let i = 0; i < Math.min(snapshot.snapshotLength, maxCount); i++) {
        const el = snapshot.snapshotItem(i);
        const item = {};
        item._text = (el.innerText || el.textContent || '').trim().slice(0, 200);
        item._tag = (el.tagName || '').toLowerCase();
        if (el.href) item._href = el.href;
        if (el.src) item._src = el.src;
        for (const attr of attrs) {
            const val = el.getAttribute ? el.getAttribute(attr) : null;
            if (val) item[attr] = val.slice(0, 200);
        }
        results.push(item);
    }
    return results;
}
"""


@register(
    name="browser_extract",
    description=(
        "批量提取页面数据。用 CSS 选择器或 XPath 匹配多个元素，提取它们的文字和属性。\n"
        "典型用法:\n"
        "- 商品列表: selector='.item-card' 或 xpath='//div[contains(@class,\"item\")]'\n"
        "- 搜索结果: selector='.search-result-item'\n"
        "- 表格行: selector='table tbody tr'\n"
        "- 价格: selector='.price' attrs=['data-price']\n"
        "- 图片: selector='img.product-img' attrs=['src','alt']\n"
        "- 链接: selector='a.product-link' attrs=['href']\n"
        "返回每个元素的文字内容和指定属性值。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "CSS 选择器（如 '.item-card' 'table tr'），与 xpath 二选一",
            },
            "xpath": {
                "type": "string",
                "description": "XPath 表达式（如 '//div[@class=\"product\"]'），与 selector 二选一",
            },
            "attrs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要提取的 HTML 属性列表（如 ['href', 'data-price', 'src']）",
            },
            "max_count": {
                "type": "integer",
                "description": "最多提取多少个元素（默认 30）",
            },
        },
    },
    risk_level="low",
)
async def browser_extract(selector: str = "", xpath: str = "",
                           attrs: list[str] = None, max_count: int = 30) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    if not selector and not xpath:
        return SkillResult(success=False, error="请指定 selector 或 xpath 其中一个")

    attrs = attrs or []
    try:
        browser = await _SharedBrowser.get()
        args = {"attrs": attrs, "maxCount": min(max_count, 100)}

        if xpath:
            args["xpath"] = xpath
            items = await browser._page.evaluate(_JS_EXTRACT_BY_XPATH, args)
            query_desc = f"XPath: {xpath}"
        else:
            args["selector"] = selector
            items = await browser._page.evaluate(_JS_EXTRACT_BY_SELECTOR, args)
            query_desc = f"CSS: {selector}"

        if not items:
            return SkillResult(success=True, data=f"未匹配到元素 ({query_desc})")

        lines = [f"提取到 {len(items)} 个元素 ({query_desc}):\n"]
        for i, item in enumerate(items, 1):
            text = item.pop("_text", "")
            tag = item.pop("_tag", "")
            href = item.pop("_href", "")
            src = item.pop("_src", "")

            parts = [f"[{i}] <{tag}>"]
            if text:
                parts.append(f'"{text[:100]}"')
            if href:
                parts.append(f"href={href[:100]}")
            if src:
                parts.append(f"src={src[:100]}")
            for k, v in item.items():
                if not k.startswith("_"):
                    parts.append(f"{k}={v}")
            lines.append("  ".join(parts))

        result = "\n".join(lines)
        if len(result) > 10000:
            result = result[:10000] + f"\n... [结果过长已截断，共 {len(items)} 条]"
        return SkillResult(success=True, data=result)
    except Exception as e:
        return SkillResult(success=False, error=f"数据提取失败: {e}")


_JS_EXTRACT_TABLE = """
(selector) => {
    const table = document.querySelector(selector || 'table');
    if (!table) return null;
    const rows = [];
    for (const tr of table.rows) {
        const cells = [];
        for (const td of tr.cells) {
            cells.push((td.innerText || '').trim().slice(0, 100));
        }
        rows.push(cells);
    }
    return rows;
}
"""


@register(
    name="browser_extract_table",
    description=(
        "提取页面上的表格数据，返回行列格式的结构化数据。"
        "不指定 selector 时提取页面上第一个 table。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "表格的 CSS 选择器（默认 'table'，即页面第一个表格）",
            },
        },
    },
    risk_level="low",
)
async def browser_extract_table(selector: str = "table") -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        rows = await browser._page.evaluate(_JS_EXTRACT_TABLE, selector)
        if not rows:
            return SkillResult(success=True, data="未找到表格或表格为空")

        lines = [f"表格数据 ({len(rows)} 行):"]
        for i, row in enumerate(rows):
            if i == 0:
                lines.append("  表头: " + " | ".join(row))
                lines.append("  " + "-" * 40)
            else:
                lines.append(f"  {i}. " + " | ".join(row))
            if i >= 50:
                lines.append(f"  ... 还有 {len(rows) - 50} 行未显示")
                break

        return SkillResult(success=True, data="\n".join(lines))
    except Exception as e:
        return SkillResult(success=False, error=f"表格提取失败: {e}")


_JS_EXTRACT_LINKS = """
(args) => {
    const {selector, maxCount} = args;
    const anchors = selector ? document.querySelectorAll(selector) : document.querySelectorAll('a[href]');
    const results = [];
    const seen = new Set();
    for (const a of anchors) {
        if (results.length >= maxCount) break;
        const href = a.href;
        if (!href || href === 'javascript:void(0)' || href === '#' || seen.has(href)) continue;
        seen.add(href);
        const text = (a.innerText || a.textContent || '').trim().slice(0, 80);
        if (!text) continue;
        results.push({text, href: href.slice(0, 200)});
    }
    return results;
}
"""


@register(
    name="browser_extract_links",
    description=(
        "提取页面上的链接列表（文字 + URL）。"
        "可以指定 CSS 选择器只提取特定区域的链接。"
        "适合提取导航菜单、搜索结果链接、商品链接等。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "限定范围的 CSS 选择器（如 '.search-results a'），留空提取全页链接",
            },
            "max_count": {
                "type": "integer",
                "description": "最多提取多少条（默认 30）",
            },
        },
    },
    risk_level="low",
)
async def browser_extract_links(selector: str = "", max_count: int = 30) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        links = await browser._page.evaluate(
            _JS_EXTRACT_LINKS, {"selector": selector, "maxCount": min(max_count, 100)}
        )
        if not links:
            return SkillResult(success=True, data="未找到链接")

        lines = [f"提取到 {len(links)} 个链接:"]
        for i, link in enumerate(links, 1):
            lines.append(f"  {i}. {link['text']} → {link['href']}")

        return SkillResult(success=True, data="\n".join(lines))
    except Exception as e:
        return SkillResult(success=False, error=f"链接提取失败: {e}")


@register(
    name="browser_scroll_collect",
    description=(
        "边滚动页面边采集数据——专为无限滚动/懒加载/电商列表设计。\n"
        "自动滚动页面，每次滚动后用 CSS 选择器提取新出现的数据项。\n"
        "适合：淘宝/闲鱼/京东/抖音的商品列表、搜索结果、信息流等。\n"
        "示例: selector='.item-card' attrs=['title', 'price'] scroll_times=5\n"
        "如果不确定选择器，先用 browser_execute_js 检查页面结构。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "selector": {
                "type": "string",
                "description": "数据项的 CSS 选择器（如 '.item-card', '.goods-item', 'li[data-sku]'）",
            },
            "sub_selectors": {
                "type": "object",
                "description": "子元素选择器映射，提取每个卡片内的字段。如 {\"title\": \".title\", \"price\": \".price\", \"link\": \"a@href\"}。键名后加 @attr 提取属性而非文字",
            },
            "scroll_times": {
                "type": "integer",
                "description": "滚动次数（默认 5，每次约一屏高度）",
            },
            "scroll_delay": {
                "type": "number",
                "description": "每次滚动后等待秒数（默认 1.5，慢网站可加大）",
            },
            "max_items": {
                "type": "integer",
                "description": "最多采集多少条（默认 100）",
            },
        },
        "required": ["selector"],
    },
    risk_level="low",
)
async def browser_scroll_collect(
    selector: str,
    sub_selectors: dict = None,
    scroll_times: int = 5,
    scroll_delay: float = 1.5,
    max_items: int = 100,
) -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    try:
        browser = await _SharedBrowser.get()
        page = browser._page

        js_collect = """
        ({selector, subSelectors, seenTexts, maxItems}) => {
            const items = document.querySelectorAll(selector);
            const results = [];
            for (const el of items) {
                if (results.length >= maxItems) break;
                const fingerprint = (el.innerText || '').trim().slice(0, 100);
                if (!fingerprint || seenTexts.includes(fingerprint)) continue;

                if (subSelectors && Object.keys(subSelectors).length > 0) {
                    const row = {};
                    for (const [key, subSel] of Object.entries(subSelectors)) {
                        let attrName = null;
                        let actualSel = subSel;
                        if (subSel.includes('@')) {
                            const parts = subSel.split('@');
                            actualSel = parts[0];
                            attrName = parts[1];
                        }
                        const sub = actualSel ? el.querySelector(actualSel) : el;
                        if (sub) {
                            row[key] = attrName ? (sub.getAttribute(attrName) || '') : (sub.innerText || sub.textContent || '').trim().slice(0, 200);
                        } else {
                            row[key] = '';
                        }
                    }
                    row['_fp'] = fingerprint;
                    results.push(row);
                } else {
                    results.push({text: fingerprint, _fp: fingerprint});
                }
            }
            return results;
        }
        """

        all_items = []
        seen_texts = []

        for i in range(scroll_times + 1):
            batch = await page.evaluate(
                js_collect,
                {
                    "selector": selector,
                    "subSelectors": sub_selectors or {},
                    "seenTexts": seen_texts,
                    "maxItems": max_items - len(all_items),
                },
            )
            for item in batch:
                fp = item.pop("_fp", "")
                if fp:
                    seen_texts.append(fp)
                all_items.append(item)

            if len(all_items) >= max_items:
                break

            if i < scroll_times:
                await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
                await asyncio.sleep(scroll_delay)

        if not all_items:
            return SkillResult(
                success=True,
                data=f"未找到匹配 `{selector}` 的数据项。\n"
                     f"建议先用 browser_execute_js('document.querySelectorAll(\"...\").length') 确认选择器是否正确。"
            )

        lines = [f"采集到 {len(all_items)} 条数据（滚动 {scroll_times} 次）:\n"]
        for idx, item in enumerate(all_items[:max_items], 1):
            if sub_selectors:
                fields = " | ".join(f"{k}={v}" for k, v in item.items() if v)
                lines.append(f"  {idx}. {fields}")
            else:
                lines.append(f"  {idx}. {item.get('text', '')}")

        return SkillResult(success=True, data="\n".join(lines))
    except Exception as e:
        return SkillResult(success=False, error=f"滚动采集失败: {e}")


# ==================== 视觉理解 ====================

@register(
    name="browser_analyze_page",
    description=(
        "截取当前页面截图并用多模态 LLM 分析页面内容。"
        "可以询问页面上的具体问题，如'有没有验证码'、'当前登录状态'、'价格是多少'。"
        "当快照文字信息不够时使用此技能获取视觉信息。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "针对页面的具体问题（可选，留空则做通用描述）",
            },
        },
    },
    risk_level="low",
    category="browser",
)
async def browser_analyze_page(question: str = "") -> SkillResult:
    if not _SharedBrowser.is_active():
        return _not_active_error()
    if not _SharedBrowser._llm_router:
        return SkillResult(success=False, error="视觉分析不可用: LLM router 未注入")

    try:
        import base64
        browser = await _SharedBrowser.get()
        await browser.wait_for_page_ready()

        screenshot_bytes = await browser._page.screenshot(
            full_page=False, type="jpeg", quality=70
        )

        max_size = 1280 * 720 * 3
        if len(screenshot_bytes) > max_size:
            await browser._page.set_viewport_size({"width": 1280, "height": 720})
            screenshot_bytes = await browser._page.screenshot(
                full_page=False, type="jpeg", quality=60
            )

        b64_image = base64.b64encode(screenshot_bytes).decode("utf-8")

        prompt = "分析这个网页截图，描述页面主要内容、可见的表单/按钮/弹窗/错误提示。"
        if question:
            prompt += f"\n用户问题: {question}"

        from ..llm import Message
        vision_msg = Message(
            role="user",
            content=prompt,
            images=[f"data:image/jpeg;base64,{b64_image}"],
        )

        response = await _SharedBrowser._llm_router.chat(
            messages=[vision_msg],
            system_prompt="你是一个网页分析助手，用中文简洁描述网页截图内容。",
        )

        analysis = response.content if response.content else "无法分析页面"
        current_url = browser._page.url
        return SkillResult(
            success=True,
            data=f"📸 页面视觉分析 ({current_url}):\n{analysis}",
        )
    except Exception as e:
        return SkillResult(success=False, error=f"视觉分析失败: {e}")
