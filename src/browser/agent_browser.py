import json
import shutil
import subprocess
import time


def resolve_invocation():
    """Return argv prefix for agent-browser.

    On Windows the npm .cmd/.ps1 shims can hang when spawned from Python
    without a console, so we prefer invoking node on the package's JS entry
    point directly. Env override: AGENT_BROWSER_JS.
    """
    import os

    override = os.environ.get("AGENT_BROWSER_JS")
    candidates = []
    if override:
        candidates.append(override)
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        candidates.append(os.path.join(appdata, "npm", "node_modules", "agent-browser", "bin", "agent-browser.js"))
    for c in candidates:
        if os.path.isfile(c):
            return ["node", c]
    cli = shutil.which("agent-browser")
    return [cli] if cli else ["agent-browser"]


BLOCK_INDICATORS = [
    "captcha",
    "security check",
    "verify you are human",
    "access denied",
    "unusual activity",
    "are you a robot",
]


class AgentBrowser:
    """Thin wrapper over the agent-browser CLI (subprocess-based).

    Only automates public pages. Never bypasses captchas or access controls;
    raises BlockedSourceError when a block is detected so callers can record
    and move on.
    """

    name = "tiktok_web"

    def __init__(self, session="research", timeout=25, logger=None):
        self.session = session
        self.timeout = timeout
        self.log = logger
        self.prefix = resolve_invocation()

    def _run(self, args, input_text=None, timeout=None):
        """Run CLI writing output to temp files.

        The agent-browser daemon can outlive the direct child and inherit
        pipe handles; using pipes makes subprocess.run block forever waiting
        for EOF. Temp files + kill-tree-on-timeout cannot hang.
        """
        import tempfile

        cmd = self.prefix + ["--session", self.session] + args
        limit = timeout or (self.timeout + 20)
        out_f = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        err_f = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=out_f,
                stderr=err_f,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
            try:
                if input_text is not None:
                    proc.stdin.write(input_text)
                    proc.stdin.close()
                rc = proc.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True, shell=False,
                )
                raise
            out_f.seek(0)
            err_f.seek(0)
            stdout_text = out_f.read()
            stderr_text = err_f.read()
            return subprocess.CompletedProcess(cmd, rc, stdout_text, stderr_text)
        finally:
            out_f.close()
            err_f.close()

    def available(self):
        try:
            proc = self._run(["--version"], timeout=15)
            return proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def open(self, url, wait="--load domcontentloaded", extra_wait_ms=1500):
        args = ["open", url]
        if wait:
            args += [wait.split(" ", 1)[0], wait.split(" ", 1)[1]] if " " in wait else [wait]
        proc = self._run(args, timeout=self.timeout + 30)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"agent-browser open failed: {err[:300]}")
        if extra_wait_ms:
            try:
                self._run(["wait", str(extra_wait_ms)], timeout=extra_wait_ms / 1000 + 10)
            except subprocess.TimeoutExpired:
                pass
        return proc.stdout.strip()

    def current_url(self):
        proc = self._run(["get", "url"], timeout=15)
        return proc.stdout.strip()

    def title(self):
        proc = self._run(["get", "title"], timeout=15)
        return proc.stdout.strip()

    def eval(self, js, timeout=None):
        proc = self._run(["eval", "--stdin"], input_text=js, timeout=timeout or self.timeout + 15)
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            raise RuntimeError(f"eval failed: {err[:300]}")
        out = proc.stdout.strip()
        try:
            return json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return out

    def scroll_bottom(self, pause_ms=1500):
        """Scroll to page bottom and let lazy content load."""
        try:
            self.eval("window.scrollTo(0, document.body.scrollHeight)")
            self._run(["wait", str(pause_ms)], timeout=pause_ms / 1000 + 10)
        except (RuntimeError, subprocess.TimeoutExpired):
            pass

    def screenshot(self, path):
        proc = self._run(["screenshot", path], timeout=self.timeout + 15)
        return proc.returncode == 0

    def detect_block(self):
        try:
            blob = (self.title() or "") + " \n " + (self.current_url() or "")
        except Exception:
            blob = ""
        low = blob.lower()
        for ind in BLOCK_INDICATORS:
            if ind in low:
                return ind
        probe = self.eval("document.body ? document.body.innerText.slice(0, 1200).toLowerCase() : ''")
        text_low = str(probe or "").lower()
        for ind in BLOCK_INDICATORS:
            if ind in text_low:
                return ind
        return None

    def visit(self, url):
        self.open(url)
        block = self.detect_block()
        if block:
            from .exceptions import BlockedSourceError
            raise BlockedSourceError(self.name, url, f"page presented protection ({block}); stopping per policy")
        return True

    def close(self):
        try:
            self._run(["close"], timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            pass


def with_retries(fn, max_retries=1, backoff_s=5, logger=None, transient=(RuntimeError, OSError, subprocess.TimeoutExpired)):
    attempt = 0
    while True:
        try:
            return fn()
        except transient as e:
            attempt += 1
            if attempt > max_retries:
                raise
            if logger:
                logger.warning(f"transient failure ({e}); retry {attempt}/{max_retries} after {backoff_s}s")
            time.sleep(backoff_s)
