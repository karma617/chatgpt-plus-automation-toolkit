from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeoutError

from .flaresolverr_client import flaresolverr_enabled, inject_flaresolverr_solution, solve_with_flaresolverr
from .hero_sms_provider import HeroSMSProvider, PhoneCountry, SmsActivation, local_phone_number, phone_matches_country
from .mail_provider import MailProvider
from .sms_provider_factory import (
    create_sms_provider,
    normalize_sms_provider_name,
    provider_country_arg,
    sms_provider_default_service,
    sms_provider_label,
)
from .storage import MailAccount
from .utils import log, random_profile


UNKNOWN_PAGE_RETRY_WAIT_MS = 5000
CLOUDFLARE_CHALLENGE_RETRYABLE = "cloudflare_turnstile_not_solved"
SIGNIN_PROBLEM_RETRY_CURRENT_FLOW = "SIGNIN_PROBLEM_RETRY_CURRENT_FLOW"
PHONE_ENTRY_ACTIONS = {"signup_phone", "phone_signup", "phone"}


def _zh(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


def is_signin_problem_retry_reason(reason: str | None) -> bool:
    return str(reason or "").strip().startswith(SIGNIN_PROBLEM_RETRY_CURRENT_FLOW)


class FatalAccountError(RuntimeError):
    pass


class ManualInterventionNeeded(RuntimeError):
    pass


class CloudflareChallengeError(RuntimeError):
    pass


class ChatGPTRegister:
    def __init__(
        self,
        page: Page,
        page_getter: Callable[[], Awaitable[Page]] | None,
        start_url: str,
        entry_action: str,
        mail_provider: MailProvider,
        age_min: int,
        age_max: int,
        sms_selection: dict[str, object] | None = None,
        log_prefix: str = "",
        proxy: str | None = None,
    ):
        self.page = page
        self.page_getter = page_getter
        self.start_url = start_url
        self.entry_action = entry_action
        self.mail_provider = mail_provider
        self.age_min = age_min
        self.age_max = age_max
        self.sms_selection = sms_selection
        self.log_prefix = log_prefix
        self.proxy = proxy
        self.generated_name: str | None = None
        self.generated_age: str | None = None
        self.bad_codes: set[str] = set()
        self.unknown_count = 0
        self.entry_count = 0
        self.phone_switch_attempts = 0
        self.signin_problem_attempts = 0

    def log(self, message: str) -> None:
        log(f"{self.log_prefix} {message}".strip())

    def is_phone_signup_mode(self) -> bool:
        return self.entry_action.lower() in PHONE_ENTRY_ACTIONS

    async def run_until_logged_in(self, account: MailAccount, since: datetime) -> None:
        if self.start_url != "current":
            await self.page.goto(self.start_url, wait_until="domcontentloaded")
        for step in range(1, 50):
            try:
                await self.refresh_page()
                await self.page.wait_for_load_state("domcontentloaded")
                state = await self.detect_state()
                self.log(f"注册状态[{step}]: {state} | url={short_url(self.page.url)}")
                if state == "fatal_account_error":
                    raise FatalAccountError(await fatal_error_message(self.page))
                if state == "logged_in":
                    self.log("已确认登录成功")
                    return
                if state == "external_oauth":
                    self.log("检测到误入第三方登录页，返回 ChatGPT 登录入口")
                    await self.page.goto(self.start_url, wait_until="domcontentloaded")
                    continue
                if state == "account_picker":
                    self.log(_zh(r"\u68c0\u6d4b\u5230\u5df2\u767b\u5f55\u8d26\u53f7\u9009\u62e9\u9875\uff0c\u5c1d\u8bd5\u81ea\u52a8\u9009\u62e9\u5f53\u524d\u8d26\u53f7"))
                    await self.handle_account_picker(account)
                    continue
                if state == "signin_problem":
                    self.log(_zh(r"\u68c0\u6d4b\u5230\u767b\u5f55\u95ee\u9898\u9875\uff0c\u5c1d\u8bd5\u70b9\u51fb\u9875\u9762\u6309\u94ae\u6062\u590d"))
                    await self.handle_signin_problem()
                    continue
                if state == "entry":
                    self.entry_count += 1
                    if self.entry_count >= 3 and self.is_phone_signup_mode():
                        self.log("入口页: 多次点击未推进，直接打开登录页再切手机注册")
                        await self.page.goto("https://chatgpt.com/auth/login", wait_until="domcontentloaded")
                        await settle(self.page)
                        continue
                    self.log(f"入口页: 点击 {self.entry_action}")
                    await self.click_entry()
                    continue
                if state == "phone_login":
                    if not self.sms_selection:
                        raise FatalAccountError("账号进入手机号登录/注册页，未启用手机号接码，按规则废弃当前账号")
                    self.phone_switch_attempts = 0
                    self.log("手机号页: 已启用接码，开始自动获取手机号")
                    await self.handle_phone_required()
                    continue
                if state == "email":
                    if self.is_phone_signup_mode():
                        if not self.sms_selection:
                            raise FatalAccountError(
                                _zh(
                                    r"\u624b\u673a\u53f7\u6ce8\u518c\u6a21\u5f0f\u672a\u542f\u7528\u63a5\u7801\u914d\u7f6e\uff0c\u7981\u6b62\u56de\u9000\u90ae\u7bb1\u6ce8\u518c"
                                )
                            )
                        await self.force_phone_login_entry()
                        continue
                    self.log(f"邮箱页: 填入邮箱 {account.email}")
                    await self.fill_email(account.email)
                    continue
                if state == "password":
                    self.log("密码页: 填入账号密码")
                    await self.fill_password(account)
                    continue
                if state == "code":
                    self.log("验证码页: 开始拉取邮箱验证码")
                    code = await self.mail_provider.wait_code(account, since, self.bad_codes)
                    self.log(f"验证码页: 已拿到验证码 {code}，准备填入")
                    accepted = await self.fill_code(code)
                    if not accepted:
                        self.bad_codes.add(code)
                        self.log(f"验证码被页面判定无效，已排除旧码: {code}")
                    else:
                        self.log("验证码页: 已提交验证码")
                    continue
                if state == "profile":
                    self.log("资料页: 准备填写姓名和年龄")
                    await self.fill_profile()
                    continue
                if state == "captcha_or_unknown":
                    self.unknown_count += 1
                    challenge_result = await self.try_cloudflare_turnstile_challenge()
                    if challenge_result == "solved":
                        self.log("[Cloudflare] Turnstile/managed challenge handled, retry page state")
                        self.unknown_count = 0
                        continue
                    if challenge_result == "blocked":
                        raise CloudflareChallengeError(CLOUDFLARE_CHALLENGE_RETRYABLE)
                    await dump_unknown_page(self.page, self.unknown_count)
                    if self.unknown_count >= 4:
                        raise ManualInterventionNeeded("连续检测到未知页/人工验证，账号已退回号池")
                    self.log(f"检测到未知页/人工验证，等待页面自动推进后重试 | 次数={self.unknown_count}/4")
                    await self.page.wait_for_timeout(UNKNOWN_PAGE_RETRY_WAIT_MS)
                    continue
                self.unknown_count = 0
            except FatalAccountError:
                raise
            except CloudflareChallengeError:
                raise
            except Exception as exc:
                if is_page_closed_error(exc):
                    self.log("页面切换/关闭中，重新绑定当前页面")
                    await self.refresh_page()
                    await self.page.wait_for_timeout(1000)
                    continue
                raise
        raise RuntimeError("注册状态机循环次数过多，疑似卡住")

    async def refresh_page(self) -> None:
        if self.page_getter:
            self.page = await self.page_getter()

    async def detect_state(self) -> str:
        url = self.page.url.lower()
        text = await body_text(self.page)
        low = text.lower()
        if any(host in url for host in ["accounts.google.", "appleid.apple.", "login.microsoftonline."]):
            return "external_oauth"
        if is_fatal_account_error(low, text):
            return "fatal_account_error"
        if await is_account_picker_page(self.page, low, text):
            return "account_picker"
        if is_signin_problem_page(low, text):
            return "signin_problem"
        if "404" in text and "找不到页面" in text:
            return "entry"
        if "chatgpt.com" in url and (
            "/g/" in url
            or "/c/" in url
            or is_chatgpt_success_landing(url, low, text)
            or await chatgpt_logged_in_markers(self.page, low, text)
        ):
            return "logged_in"
        if await visible_input_count(self.page, r"password") > 0 and not likely_code_page(low):
            return "password"
        if likely_code_page(low) or await visible_code_inputs(self.page) > 0:
            return "code"
        if await is_phone_login_page(self.page, low, text):
            return "phone_login"
        if is_entry_page(low, text):
            return "entry"
        if await page_looks_like_profile_page(self.page):
            return "profile"
        if await visible_input_count(self.page, r"email|username") > 0:
            return "email"
        if any(key in low for key in ["tell us about yourself", "full name", "birthday", "date of birth", "age"]) or any(
            key in text for key in ["姓名", "名字", "年龄", "生日", "出生"]
        ):
            return "profile"
        return "captcha_or_unknown"

    async def click_entry(self) -> None:
        entry_action = self.entry_action.lower()
        if entry_action in {"login", "signin", "log_in"}:
            labels = ["登录", "Log in", "Login", "Sign in", _zh(r"\u30ed\u30b0\u30a4\u30f3")]
            patterns = [re.compile(r"log in|login|sign in", re.I), re.compile(r"登录"), re.compile(_zh(r"\u30ed\u30b0\u30a4\u30f3"))]
        elif entry_action in {"signup_phone", "phone_signup", "phone"}:
            labels = [
                "登录", "Log in", "Login", "Sign in", "免费注册", "创建账号", "注册", "Sign up", "Create account",
                _zh(r"\u30ed\u30b0\u30a4\u30f3"), _zh(r"\u65b0\u898f\u767b\u9332"),
                _zh(r"\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u4f5c\u6210"),
                _zh(r"\u30ed\u30b0\u30a4\u30f3\u307e\u305f\u306f\u65b0\u898f\u767b\u9332"),
            ]
            patterns = [
                re.compile(r"log in|login|sign in|sign up|create account|register", re.I),
                re.compile(r"登录|免费注册|创建账号|注册"),
                re.compile(_zh(r"\u30ed\u30b0\u30a4\u30f3|\u65b0\u898f\u767b\u9332|\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u4f5c\u6210")),
            ]
        else:
            labels = [
                "免费注册", "创建账号", "创建帐户", "注册", "Sign up for free", "Sign up", "Create account", "Register",
                _zh(r"\u65b0\u898f\u767b\u9332"), _zh(r"\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u4f5c\u6210"),
                _zh(r"\u30ed\u30b0\u30a4\u30f3\u307e\u305f\u306f\u65b0\u898f\u767b\u9332"),
            ]
            patterns = [
                re.compile(r"sign up|create account|create|register|free", re.I),
                re.compile(r"免费注册|创建账号|创建帐户|注册|创建"),
                re.compile(_zh(r"\u65b0\u898f\u767b\u9332|\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u4f5c\u6210")),
            ]
        for pattern in patterns:
            buttons = self.page.get_by_role("button", name=pattern)
            for index in range(await buttons.count()):
                button = buttons.nth(index)
                try:
                    if await button.is_visible() and await button.is_enabled():
                        await button.click()
                        await settle(self.page)
                        return
                except Exception:
                    continue
            links = self.page.get_by_role("link", name=pattern)
            for index in range(await links.count()):
                link = links.nth(index)
                try:
                    if await link.is_visible():
                        await link.click()
                        await settle(self.page)
                        return
                except Exception:
                    continue
        for label in labels:
            if await click_by_visible_text(self.page, label):
                await settle(self.page)
                return
        clicked = await self.page.evaluate(
            """(labels) => {
                const normalized = (s) => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const wanted = labels.map(normalized);
                const nodes = [...document.querySelectorAll('button, a, [role="button"], div, span')];
                for (const node of nodes) {
                    const text = normalized(node.innerText || node.textContent || '');
                    if (!text || !wanted.some((label) => text === label || text.includes(label))) continue;
                    const rect = node.getBoundingClientRect();
                    const style = getComputedStyle(node);
                    if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden' || style.display === 'none') continue;
                    node.click();
                    return true;
                }
                return false;
            }""",
            labels,
        )
        if clicked:
            await settle(self.page)
            return
        if entry_action in {"signup_phone", "phone_signup", "phone"}:
            clicked = await self.page.evaluate(
                """() => {
                    const visible = (el) => {
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const nodes = [...document.querySelectorAll('a[href], button, [role="button"]')].filter(visible);
                    const target = nodes.find((el) => {
                        const href = String(el.getAttribute('href') || '');
                        return href.includes('/auth/login') || href.includes('/log-in-or-create-account') || href.includes('/create-account');
                    }) || nodes.find((el) => /login|log in|登录|sign up|注册|创建|\u30ed\u30b0\u30a4\u30f3|\u65b0\u898f\u767b\u9332|\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u4f5c\u6210/i.test(el.innerText || el.textContent || el.getAttribute('aria-label') || ''));
                    if (!target) return false;
                    target.click();
                    return true;
                }"""
            )
            if clicked:
                await settle(self.page)
                return
        raise RuntimeError(f"入口页未找到可点击按钮: entry_action={self.entry_action}")

    async def handle_account_picker(self, account: MailAccount) -> None:
        mode = self.entry_action.lower()
        clicked = await click_account_picker_session(
            self.page,
            account.email,
            allow_single_fallback=mode in {"login", "signin", "log_in"},
        )
        if clicked:
            self.log(_zh(r"\u8d26\u53f7\u9009\u62e9\u9875: \u5df2\u70b9\u51fb\u5df2\u767b\u5f55\u8d26\u53f7"))
            return
        if mode in {"login", "signin", "log_in"}:
            if await click_account_picker_link(self.page, "login"):
                self.log(_zh(r"\u8d26\u53f7\u9009\u62e9\u9875: \u672a\u5339\u914d\u5230\u5f53\u524d\u8d26\u53f7\uff0c\u5df2\u5207\u6362\u5230\u5176\u4ed6\u8d26\u53f7\u767b\u5f55"))
                return
        if await click_account_picker_link(self.page, "create"):
            self.log(_zh(r"\u8d26\u53f7\u9009\u62e9\u9875: \u672a\u5339\u914d\u5230\u5f53\u524d\u8d26\u53f7\uff0c\u5df2\u5207\u6362\u5230\u521b\u5efa\u8d26\u53f7"))
            return
        raise RuntimeError("ACCOUNT_PICKER_NO_USABLE_ACCOUNT")

    async def handle_signin_problem(self) -> None:
        self.signin_problem_attempts += 1
        if self.signin_problem_attempts > 2:
            raise RuntimeError(f"{SIGNIN_PROBLEM_RETRY_CURRENT_FLOW}: repeated signin problem page")
        before_url = self.page.url
        before_text = await body_text(self.page)
        clicked = await click_signin_problem_action(self.page)
        if not clicked:
            raise RuntimeError(f"{SIGNIN_PROBLEM_RETRY_CURRENT_FLOW}: problem page button not found")
        await self.page.wait_for_timeout(3500)
        await settle(self.page)
        await self.refresh_page()
        after_text = await body_text(self.page)
        if is_signin_problem_page(after_text.lower(), after_text):
            raise RuntimeError(f"{SIGNIN_PROBLEM_RETRY_CURRENT_FLOW}: problem page button no response")
        if self.page.url == before_url and after_text.strip()[:500] == before_text.strip()[:500]:
            raise RuntimeError(f"{SIGNIN_PROBLEM_RETRY_CURRENT_FLOW}: problem page unchanged")
        self.log(_zh(r"\u767b\u5f55\u95ee\u9898\u9875: \u9875\u9762\u5df2\u54cd\u5e94\uff0c\u7ee7\u7eed\u68c0\u6d4b\u767b\u5f55\u72b6\u6001"))

    async def try_cloudflare_turnstile_challenge(self, timeout_ms: int = 38_000) -> str:
        state = await cloudflare_turnstile_state(self.page)
        if not state.get("present"):
            return "absent"
        token_len = int(state.get("tokenLength") or 0)
        self.log(
            f"[Cloudflare] Turnstile/managed challenge detected "
            f"token_len={token_len} iframe={state.get('iframeCount')} widget={state.get('widgetCount')} "
            f"url={short_url(self.page.url)}"
        )
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        last_click = 0.0
        click_count = 0
        last_token_len = token_len
        stagnant_since = asyncio.get_running_loop().time()
        no_widget_logged = False
        while asyncio.get_running_loop().time() < deadline:
            state = await cloudflare_turnstile_state(self.page)
            if not state.get("present"):
                await settle(self.page)
                return "solved"
            if state.get("successVisible"):
                self.log("[Cloudflare] managed challenge success text visible, wait auth redirect")
                await self.page.wait_for_timeout(3000)
                state_name = await self.detect_state()
                if state_name != "captcha_or_unknown":
                    self.log(f"[Cloudflare] challenge page advanced to state={state_name}")
                    return "solved"
                continue
            token_len = int(state.get("tokenLength") or 0)
            if token_len >= 80:
                await sync_cloudflare_turnstile_token(self.page)
                await settle(self.page)
                return "solved"
            if token_len != last_token_len:
                last_token_len = token_len
                stagnant_since = asyncio.get_running_loop().time()
            now = asyncio.get_running_loop().time()
            if state.get("clickableHint") and click_count < 2 and now - last_click >= 12:
                last_click = now
                click_result = await click_cloudflare_turnstile_widget(self.page)
                if click_result:
                    click_count += 1
                    self.log(f"[Cloudflare] clicked Turnstile/managed challenge widget ({click_count}/2)")
                else:
                    if not no_widget_logged:
                        no_widget_logged = True
                        self.log("[Cloudflare] no clickable Turnstile widget found, wait for auto challenge")
            elif not state.get("clickableHint") and not no_widget_logged:
                no_widget_logged = True
                self.log("[Cloudflare] managed challenge has no visible widget/iframe, wait for auto challenge")
            await self.page.wait_for_timeout(1500)
            state_name = await self.detect_state()
            if state_name != "captcha_or_unknown":
                self.log(f"[Cloudflare] challenge page advanced to state={state_name}")
                return "solved"
            if (click_count >= 2 or not state.get("clickableHint")) and now - stagnant_since >= 22:
                self.log("[Cloudflare] challenge token not progressing after low-frequency clicks; try FlareSolverr or rotate proxy/session")
                break
        state = await cloudflare_turnstile_state(self.page)
        if int(state.get("tokenLength") or 0) >= 80 or not state.get("present"):
            return "solved"
        if await self.try_flaresolverr_challenge():
            return "solved"
        return "blocked"

    async def try_flaresolverr_challenge(self) -> bool:
        if not flaresolverr_enabled():
            self.log("[Cloudflare] FlareSolverr disabled, rotate proxy/session")
            return False
        target_url = self.page.url or self.start_url or "https://chatgpt.com/"
        self.log("[Cloudflare] FlareSolverr enabled, requesting challenge solution")
        try:
            result = await asyncio.to_thread(solve_with_flaresolverr, target_url, proxy=self.proxy)
        except Exception as exc:  # noqa: BLE001
            self.log(f"[Cloudflare] FlareSolverr exception: {exc}")
            return False
        if not result.ok:
            self.log(f"[Cloudflare] FlareSolverr failed: {result.reason}")
            return False
        try:
            await inject_flaresolverr_solution(self.page, result)
            self.log(
                f"[Cloudflare] FlareSolverr cookies injected: cookies={len(result.cookies)} "
                f"status={result.status_code or ''}"
            )
            await self.page.goto(target_url, wait_until="domcontentloaded", timeout=45_000)
            await self.page.wait_for_timeout(2500)
            state = await cloudflare_turnstile_state(self.page)
            if not state.get("present") or int(state.get("tokenLength") or 0) >= 80:
                return True
            self.log(
                f"[Cloudflare] FlareSolverr cookies injected but challenge still present "
                f"token_len={state.get('tokenLength')} iframe={state.get('iframeCount')} widget={state.get('widgetCount')}"
            )
            return False
        except Exception as exc:  # noqa: BLE001
            self.log(f"[Cloudflare] FlareSolverr cookie replay failed: {exc}")
            return False

    async def click_email_switch(self) -> None:
        if await click_by_visible_text(self.page, "继续使用电子邮件地址登录"):
            await settle(self.page)
            return
        if await click_by_visible_text(self.page, "Continue with email"):
            await settle(self.page)
            return
        if await click_by_visible_text(self.page, _zh(r"\u30e1\u30fc\u30eb\u30a2\u30c9\u30ec\u30b9\u3067\u7d9a\u884c")):
            await settle(self.page)
            return
        clicked = await self.page.evaluate(
            """() => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const nodes = [...document.querySelectorAll('button, a, [role="button"], div')];
                const target = nodes.find((el) => visible(el) && /电子邮件|邮箱|email|\u30e1\u30fc\u30eb/i.test(el.innerText || el.textContent || el.getAttribute('aria-label') || ''));
                if (!target) return false;
                target.click();
                return true;
            }"""
        )
        if clicked:
            await settle(self.page)
            return
        raise RuntimeError("电话登录页未找到切换到邮箱登录按钮")

    async def force_phone_login_entry(self) -> None:
        self.phone_switch_attempts += 1
        if self.phone_switch_attempts > 3:
            raise FatalAccountError(
                _zh(
                    r"\u624b\u673a\u53f7\u6ce8\u518c\u6a21\u5f0f\u672a\u80fd\u5207\u6362\u5230\u624b\u673a\u53f7\u767b\u5f55\u9875\uff0c\u7981\u6b62\u56de\u9000\u90ae\u7bb1\u6ce8\u518c"
                )
            )
        self.log(
            _zh(r"\u90ae\u7bb1\u9875: Free \u6ce8\u518c\u914d\u7f6e\u4e3a\u624b\u673a\u53f7\u6ce8\u518c\uff0c\u5f3a\u5236\u5207\u6362\u5230\u624b\u673a\u53f7\u767b\u5f55")
            + f" ({self.phone_switch_attempts}/3)"
        )
        if await self.click_phone_switch():
            text = await body_text(self.page)
            if await is_phone_login_page(self.page, text.lower(), text):
                return
            self.log(
                _zh(
                    r"\u624b\u673a\u53f7\u5207\u6362\u6309\u94ae\u5df2\u70b9\u51fb\uff0c\u4f46\u9875\u9762\u4ecd\u672a\u8fdb\u5165\u624b\u673a\u53f7\u8f93\u5165\uff0c\u5c1d\u8bd5\u76f4\u63a5\u6253\u5f00\u624b\u673a\u53f7\u5165\u53e3"
                )
            )
        phone_url = "https://chatgpt.com/auth/login?usernamekind=phone_number"
        if self.phone_switch_attempts >= 2:
            phone_url = "https://chatgpt.com/auth/login?screen_hint=phone"
        await self.page.goto(phone_url, wait_until="domcontentloaded")
        await settle(self.page)

    async def click_phone_switch(self) -> bool:
        labels = [
            _zh(r"\u4f7f\u7528\u7535\u8bdd\u53f7\u7801\u7ee7\u7eed"),
            _zh(r"\u4f7f\u7528\u624b\u673a\u53f7\u7ee7\u7eed"),
            _zh(r"\u624b\u673a\u767b\u5f55"),
            _zh(r"\u624b\u673a\u53f7\u767b\u5f55"),
            _zh(r"\u7ee7\u7eed\u4f7f\u7528\u624b\u673a\u767b\u5f55"),
            _zh(r"\u96fb\u8a71\u756a\u53f7\u3067\u7d9a\u884c"),
            _zh(r"\u96fb\u8a71\u756a\u53f7\u3067\u30ed\u30b0\u30a4\u30f3"),
            _zh(r"\u96fb\u8a71\u756a\u53f7"),
            _zh(r"\u643a\u5e2f\u96fb\u8a71\u3067\u7d9a\u884c"),
            "Continue with phone",
            "Continue with phone number",
            "Phone number",
            "Phone",
        ]
        for label in labels:
            if await click_by_visible_text(self.page, label):
                await settle(self.page)
                return True
        clicked = await self.page.evaluate(
            """() => {
                const visible = (el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const nodes = [...document.querySelectorAll('button, a, [role="button"], div')];
                const textOf = (el) => [
                    el.innerText || '',
                    el.textContent || '',
                    el.getAttribute('aria-label') || '',
                    el.getAttribute('title') || '',
                    el.outerHTML || ''
                ].join(' ');
                const target = nodes.find((el) => visible(el) && /\u624b\u673a|\u624b\u673a\u53f7|\u7535\u8bdd\u53f7\u7801|\u96fb\u8a71\u756a\u53f7|\u643a\u5e2f\u96fb\u8a71|\u643a\u5e2f|phone|mobile|tel/i.test(textOf(el)));
                if (!target) return false;
                target.click();
                return true;
            }"""
        )
        if clicked:
            await settle(self.page)
            return True
        return False

    async def fill_email(self, email: str) -> None:
        locator = await first_visible(
            self.page.locator("input[type='email'], input[name*='email' i], input[autocomplete='username']")
        )
        if not locator:
            locator = await first_textbox(self.page)
        if not locator:
            raise RuntimeError("未找到邮箱输入框")
        await human_fill(locator, email, force_mouse=True)
        if not await click_email_submit_safe(self.page, locator):
            raise RuntimeError("邮箱页未找到安全的继续按钮")

    async def fill_password(self, account: MailAccount) -> None:
        if not account.password:
            raise FatalAccountError("账号进入密码页，但账号池没有 password；判定为已注册/不可用，跳过当前账号")
        locator = await first_visible(self.page.locator("input[type='password']"))
        if not locator:
            raise RuntimeError("未找到密码输入框")
        await human_fill(locator, account.password, force_mouse=True)
        await click_submit_by_js(
            self.page,
            ["下一步", "继续", "登录", "Continue", "Next", "Log in", _zh(r"\u7d9a\u884c"), _zh(r"\u6b21\u3078"), _zh(r"\u30ed\u30b0\u30a4\u30f3")],
        )

    async def fill_code(self, code: str) -> bool:
        inputs = await visible_locators(self.page.locator("input:not([type='file'])"))
        code_inputs = []
        for item in inputs:
            attrs = await input_attrs(item)
            if "password" in attrs.get("type", "").lower():
                continue
            if any(k in " ".join(attrs.values()).lower() for k in ["code", "otp", "verification", "one-time"]):
                code_inputs.append(item)
        if len(code_inputs) >= 6:
            for index, char in enumerate(code[:6]):
                await code_inputs[index].fill(char)
        elif code_inputs:
            await human_fill(code_inputs[0], code, force_mouse=True)
        else:
            target = await first_textbox(self.page)
            if not target:
                raise RuntimeError("未找到验证码输入框")
            await human_fill(target, code, force_mouse=True)
        await click_continue(self.page)
        return not await is_invalid_code_page(self.page)

    async def fill_profile(self) -> None:
        if not self.generated_name or not self.generated_age:
            self.generated_name, self.generated_age = random_profile(self.age_min, self.age_max)
            self.log(f"已生成资料: {self.generated_name} / {self.generated_age}")
        if await fill_profile_stable_fields(self.page, self.generated_name, self.generated_age, self.log):
            await click_profile_submit_by_js(self.page)
            self.log("资料页: 已点击完成创建")
            return
        if await fill_profile_by_js(self.page, self.generated_name, self.generated_age, self.log):
            await click_profile_submit_by_js(self.page)
            self.log("资料页: 已点击完成创建")
            return
        raise RuntimeError("资料页未能自动填入姓名和年龄")

    async def handle_phone_required(self) -> None:
        selection = self.sms_selection or {}
        provider_name = normalize_sms_provider_name(str(selection.get("provider") or "herosms"))
        provider_label = str(selection.get("provider_label") or sms_provider_label(provider_name))
        api_key = str(selection.get("api_key") or "").strip()
        default_service = sms_provider_default_service(provider_name)
        service = str(selection.get("service") or default_service).strip() or default_service
        country = selection.get("country")
        operator = selection.get("operator")
        if not api_key or not isinstance(country, PhoneCountry):
            raise FatalAccountError("手机号接码配置不完整，无法处理手机号必填页")
        operator_value = str(getattr(operator, "operator", "") or "").strip()
        # 5sim 不接受空 operator，默认 "any"
        if provider_name == "fivesim" and not operator_value:
            operator_value = "any"
        operator_label = str(getattr(operator, "label", "") or operator_value or "任何运营商")
        poll_interval = float(selection.get("poll_interval") or 5.0)
        max_attempts = int(selection.get("max_attempts") or 60)
        base_url = str(selection.get("base_url") or "").strip()
        provider = create_sms_provider(
            provider_name,
            api_key,
            base_url=base_url,
            pool_file=base_url if provider_name == "chatgpt-api" else "",
        )
        # 5sim 用 slug；HeroSMS/Grizzly 用 hero_sms_country int
        country_arg = provider_country_arg(provider_name, country)
        activation: SmsActivation | None = None
        try:
            self.log(
                f"手机号页: {provider_label} 自动接码启动 | service={service}, "
                f"国家={country.name}(+{country.dial_code}, ID={country.hero_sms_country}), 服务商={operator_label}"
            )
            activation = await asyncio.to_thread(
                provider.get_number,
                service,
                country_arg,
                operator=operator_value,
            )
            self.log(f"手机号页: 已获取手机号 {activation.phone_number}，activation={activation.activation_id}")
            if not phone_matches_country(activation.phone_number, country):
                bad_phone = activation.phone_number
                await asyncio.to_thread(provider.cancel, activation.activation_id)
                activation = None
                raise RuntimeError(f"PHONE_COUNTRY_MISMATCH: phone={bad_phone}, target=+{country.dial_code}")
            await asyncio.to_thread(provider.mark_ready, activation.activation_id)
            await fill_phone_and_wait_sms_page(self.page, activation.phone_number, country, self.log)
            if await page_looks_like_create_password(self.page):
                password = str(selection.get("password") or "").strip()
                if not password:
                    raise RuntimeError("手机号注册进入创建密码页，但当前流程未提供密码")
                self.log("手机号页: 检测到创建密码页，先填入注册密码")
                await fill_create_password_page(self.page, password, self.log)
            if not await wait_for_sms_verification_page(self.page, self.log):
                self.log("手机号页: 未明确识别到短信验证码页，仍继续尝试拉取验证码")
            self.log(f"手机号页: 开始拉取短信验证码，间隔={poll_interval:g}s，最多={max_attempts}次")
            code = await asyncio.to_thread(
                provider.poll_for_code,
                activation.activation_id,
                interval=poll_interval,
                max_attempts=max_attempts,
            )
            self.log(f"手机号页: 已拉取到短信验证码 {code}，准备填入")
            await fill_sms_code(self.page, code, self.log)
            status, detail = await wait_for_code_submit_result(self.page, timeout=12)
            if status == "invalid":
                raise RuntimeError(f"短信验证码无效或过期: {detail}")
            if status == "pending":
                self.log("手机号页: 验证码已提交，页面暂未明确推进，继续状态机观察")
            else:
                self.log("手机号页: 验证码提交成功，页面已推进")
            if selection.get("defer_sms_complete"):
                selection["last_phone"] = activation.phone_number
                selection["last_activation"] = activation
                selection["last_sms_code"] = code
                self.log("手机号页: 当前流程要求延后完成短信激活")
            else:
                await asyncio.to_thread(provider.complete, activation.activation_id)
        except Exception as exc:
            if activation:
                await asyncio.to_thread(provider.cancel, activation.activation_id)
                selection.pop("last_activation", None)
            raise FatalAccountError(f"手机号接码失败: {exc}") from exc


async def find_phone_input(page: Page) -> Locator | None:
    selectors = (
        'input#phoneNumberInput',
        'input[name="phoneNumberInput"]',
        'input[autocomplete="tel"]',
        'input[type="tel"]',
        'input[inputmode="tel"]',
        'input[name*="phone" i]',
        'input[id*="phone" i]',
        'input[aria-label*="phone" i]',
        'input[aria-label*="\u96fb\u8a71\u756a\u53f7"]',
        'input[placeholder*="phone" i]',
        'input[placeholder*="手机号"]',
        'input[placeholder*="电话号码"]',
        'input[placeholder*="\u96fb\u8a71\u756a\u53f7"]',
    )
    for selector in selectors:
        found = await maybe_visible_selector(page, selector, timeout=900)
        if found:
            return found
    return None


async def page_looks_like_profile_page(page: Page) -> bool:
    lower_url = (page.url or "").lower()
    if "/about-you" in lower_url:
        return True
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el || !el.getBoundingClientRect) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const nameInput = document.querySelector(
                        'input[name="name"], input[autocomplete="name"], input[id*="name" i], ' +
                        'input[aria-label*="name" i], input[placeholder*="name" i], input[aria-label*="\\u6c0f\\u540d"], ' +
                        'input[placeholder*="\\u6c0f\\u540d"], input[aria-label*="\\u540d\\u524d"], input[placeholder*="\\u540d\\u524d"]'
                    );
                    const ageInput = document.querySelector(
                        'input[name="age"], input[inputmode="numeric"], input[type="number"], input[id*="age" i], ' +
                        'input[aria-label*="age" i], input[placeholder*="age" i], input[aria-label*="\\u5e74\\u9f62"], ' +
                        'input[placeholder*="\\u5e74\\u9f62"]'
                    );
                    if (visible(nameInput) && visible(ageInput)) return true;
                    const text = String(document.body?.innerText || '').toLowerCase();
                    return (
                        text.includes('tell us about yourself') ||
                        text.includes('full name') ||
                        text.includes('date of birth') ||
                        text.includes('birthday') ||
                        text.includes('\\u6c0f\\u540d') ||
                        text.includes('\\u5e74\\u9f62') ||
                        text.includes('\\u751f\\u5e74\\u6708\\u65e5')
                    );
                }"""
            )
        )
    except Exception:
        return False


async def select_phone_country(page: Page, country: PhoneCountry, logger: Callable[[str], None]) -> None:
    if not country.dial_code and not country.iso_code:
        return
    logger(f"手机号页: 选择国家 {country.name} +{country.dial_code}")
    try:
        already = await page.evaluate(
            """(code) => {
                for (const node of document.querySelectorAll('button[aria-haspopup="listbox"], button, .react-aria-SelectValue')) {
                    const text = (node.innerText || node.textContent || '').trim();
                    if (text.includes(`+${code}`) || text.includes(`(${code})`)) return text;
                }
                return '';
            }""",
            country.dial_code,
        )
        if already:
            logger(f"手机号页: 页面国家已匹配 {already}")
            return
    except Exception:
        pass

    try:
        changed = await page.evaluate(
            """({ iso, code, name }) => {
                const select = document.querySelector('select');
                if (!select) return '';
                const options = Array.from(select.options || []);
                let target = null;
                if (iso) target = options.find(opt => String(opt.value || '').toUpperCase() === iso);
                if (!target && name) target = options.find(opt => (opt.text || '').includes(name));
                if (!target && code) target = options.find(opt => (opt.text || '').includes(`+${code}`) || (opt.text || '').includes(`+(${code})`) || (opt.text || '').includes(`(${code})`));
                if (!target) return '';
                const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set;
                if (setter) setter.call(select, target.value);
                else select.value = target.value;
                select.dispatchEvent(new Event('input', { bubbles: true }));
                select.dispatchEvent(new Event('change', { bubbles: true }));
                for (const b of document.querySelectorAll('button')) {
                    const text = (b.innerText || b.textContent || '').trim();
                    if (text.includes(`+${code}`) || text.includes(`+(${code})`) || text.includes(`(${code})`)) return text;
                }
                return target.text || target.value;
            }""",
            {"iso": country.iso_code, "code": country.dial_code, "name": country.name},
        )
        if changed and (
            f"+{country.dial_code}" in changed
            or f"+({country.dial_code})" in changed
            or f"({country.dial_code})" in changed
            or country.iso_code in changed
            or country.name in changed
        ):
            logger(f"手机号页: 已通过 select 选择国家 {changed}")
            await settle(page)
            if await country_selector_matches(page, country):
                return
    except Exception as exc:
        logger(f"手机号页: select 国家选择失败，继续备用方式: {short_error_text(exc)}")

    try:
        button = page.locator('button[aria-haspopup="listbox"]').filter(has_text=re.compile(r"\+\(?\d")).first
        if await button.is_visible(timeout=1000):
            await button.click(timeout=3000)
            await page.wait_for_timeout(1500)
            target = await page.evaluate(
                """({ iso, code, name }) => {
                    const select = document.querySelector('select');
                    const options = Array.from(select?.options || []);
                    let index = -1;
                    let value = iso || '';
                    for (let i = 0; i < options.length; i += 1) {
                        const text = options[i].text || '';
                        const optionValue = String(options[i].value || '').toUpperCase();
                        if ((iso && optionValue === iso) || (name && text.includes(name)) || (code && (text.includes(`+${code}`) || text.includes(`+(${code})`) || text.includes(`(${code})`)))) {
                            index = i;
                            value = options[i].value || value;
                            break;
                        }
                    }
                    return { index, value };
                }""",
                {"iso": country.iso_code, "code": country.dial_code, "name": country.name},
            )
            if isinstance(target, dict) and int(target.get("index", -1)) >= 0:
                await page.evaluate(
                    """(idx) => {
                        const listbox = document.querySelector('[role="listbox"]');
                        if (!listbox) return;
                        let scroller = listbox;
                        while (scroller && scroller !== document.body) {
                            const style = getComputedStyle(scroller);
                            if (style.overflow === 'auto' || style.overflow === 'scroll' || style.overflowY === 'auto' || style.overflowY === 'scroll') break;
                            scroller = scroller.parentElement;
                        }
                        if (scroller) scroller.scrollTop = idx * 40;
                    }""",
                    int(target["index"]),
                )
                await page.wait_for_timeout(1000)
                value = str(target.get("value") or country.iso_code)
                option_box = await page.evaluate(
                    """({ value, code, name }) => {
                        const visible = (el) => {
                            const rect = el.getBoundingClientRect();
                            const style = getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                        };
                        const selectors = value ? [`[data-key="${CSS.escape(value)}"]`, `[data-value="${CSS.escape(value)}"]`, `[value="${CSS.escape(value)}"]`] : [];
                        for (const selector of selectors) {
                            const option = document.querySelector(selector);
                            if (option && visible(option)) {
                                const rect = option.getBoundingClientRect();
                                return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, text: option.innerText || option.textContent || value };
                            }
                        }
                        for (const option of document.querySelectorAll('[role="option"], li, div')) {
                            const text = (option.innerText || option.textContent || '').trim();
                            if (!visible(option)) continue;
                            if ((name && text.includes(name)) || (code && (text.includes(`+${code}`) || text.includes(`+(${code})`) || text.includes(`(${code})`)))) {
                                const rect = option.getBoundingClientRect();
                                return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2, text };
                            }
                        }
                        return null;
                    }""",
                    {"value": value, "code": country.dial_code, "name": country.name},
                )
                if option_box:
                    await page.mouse.click(option_box["x"], option_box["y"])
                    logger(f"手机号页: 已通过下拉选择国家 {option_box.get('text')}")
                    await settle(page)
                    if await country_selector_matches(page, country):
                        return
    except Exception as exc:
        logger(f"手机号页: 下拉国家选择失败: {short_error_text(exc)}")
    raise RuntimeError(f"国家选择失败，未能切换到 {country.name} +{country.dial_code}")


async def country_selector_matches(page: Page, country: PhoneCountry) -> bool:
    try:
        current = await current_phone_country_code(page)
        return bool(country.dial_code and current == country.dial_code)
    except Exception:
        return False


async def click_phone_submit(page: Page, field: Locator | None = None) -> bool:
    clicked = await click_submit_by_js(
        page,
        ["继续", "Continue", "Next", "Verify", "Submit", "验证", "下一步", _zh(r"\u7d9a\u884c"), _zh(r"\u6b21\u3078"), _zh(r"\u78ba\u8a8d"), _zh(r"\u9001\u4fe1")],
    )
    if clicked:
        return True
    if field is not None:
        try:
            await field.press("Enter")
            await settle(page)
            return True
        except Exception:
            pass
    return False


async def ensure_sms_channel_selected(page: Page, logger: Callable[[str], None]) -> None:
    try:
        changed = await page.evaluate(
            """() => {
                const input = document.querySelector('input[type="radio"][value="sms"], input[name="channel"][value="sms"]');
                if (!input) return false;
                if (input.checked) return true;
                const label = input.closest('label');
                const target = label || input;
                target.dispatchEvent(new MouseEvent('pointerdown', { bubbles: true, cancelable: true, view: window }));
                target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                if (!input.checked) {
                    input.checked = true;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }
                const hidden = document.querySelector('input[name="channel"]');
                if (hidden && hidden.value !== 'sms') {
                    hidden.value = 'sms';
                    hidden.dispatchEvent(new Event('input', { bubbles: true }));
                    hidden.dispatchEvent(new Event('change', { bubbles: true }));
                }
                return true;
            }"""
        )
        if changed:
            logger("鎵嬫満鍙烽〉: 宸插垏鎹㈤獙璇佺爜鍙戦€佹柟寮忎负鐭俊")
            await page.wait_for_timeout(500)
    except Exception as exc:
        logger(f"鎵嬫満鍙烽〉: 鐭俊閫氶亾鍒囨崲澶辫触锛岀户缁彁浜? {short_error_text(exc)}")


async def fill_phone_and_wait_sms_page(page: Page, phone: str, country: PhoneCountry, logger: Callable[[str], None]) -> None:
    logger(f"手机号页: 准备填入手机号 | 国家={country.name}, ISO={country.iso_code or '-'}, 区号=+{country.dial_code or '-'}")
    await select_phone_country(page, country, logger)
    phone_input = await find_phone_input(page)
    if not phone_input:
        raise RuntimeError("未找到手机号输入框")
    logger("手机号页: 已找到手机号输入框")
    current_country = await current_phone_country_code(page)
    if country.dial_code and current_country != country.dial_code:
        raise RuntimeError(f"国家选择未生效：页面=+{current_country or '-'}，目标=+{country.dial_code}")
    if not phone_matches_country(phone, country):
        raise RuntimeError(f"PHONE_COUNTRY_MISMATCH: phone={phone}, target=+{country.dial_code}")
    value = local_phone_number(phone, country)
    logger(f"手机号页: 接码号码={phone}，页面国家=+{current_country or '-'}，输入本地号码={value}")
    await human_fill(phone_input, value, force_mouse=True)
    await ensure_sms_channel_selected(page, logger)
    logger("手机号页: 手机号已填入页面")
    if not await click_phone_submit(page, phone_input):
        raise RuntimeError("手机号提交按钮点击失败")
    logger("手机号页: 已点击继续/提交，等待短信验证码页")
    await page.wait_for_timeout(3000)
    if await page_looks_like_phone_rejected(page):
        raise RuntimeError("PHONE_REJECTED: phone rejected by page after submit")


async def find_sms_code_input(page: Page) -> Locator | None:
    selectors = (
        'input[name="code"]',
        'input[autocomplete="one-time-code"]',
        'input[inputmode="numeric"]',
        'input[placeholder*="验证码"]',
        'input[placeholder*="code" i]',
        'input[type="tel"]',
        'input[type="text"]',
        'input[type="number"]',
    )
    for selector in selectors:
        locators = page.locator(selector)
        for index in range(min(await locators.count(), 8)):
            locator = locators.nth(index)
            try:
                if not await locator.is_visible(timeout=400):
                    continue
                name = await locator.get_attribute("name", timeout=400) or ""
                if name == "phoneNumberInput":
                    continue
                return locator
            except Exception:
                continue
    return None


async def page_looks_like_sms_verification(page: Page) -> bool:
    lower_url = (page.url or "").lower()
    if "contact-verification" in lower_url or "phone-verification" in lower_url:
        return True
    text = (await body_text(page)).lower()
    return bool(await find_sms_code_input(page) and any(hint in text for hint in ("sms", "text message", "verification code", "验证码", "短信")))


async def page_looks_like_phone_rejected(page: Page) -> bool:
    text = (await body_text(page)).lower()
    return any(
        hint in text
        for hint in (
            "invalid phone number",
            "phone number is not valid",
            "this phone number is not supported",
            "try a different phone number",
            "use a different phone number",
            "手机号无效",
            "电话号码无效",
            "电话号码不可用",
        )
    )


async def wait_for_sms_verification_page(page: Page, logger: Callable[[str], None], timeout: int = 45) -> bool:
    for index in range(max(1, timeout)):
        if await page_looks_like_create_password(page):
            logger(f"手机号页: 当前是创建密码页，不是短信验证码页 url={short_url(page.url)}")
            return False
        if await page_looks_like_sms_verification(page):
            logger("手机号页: 页面已进入短信验证码阶段")
            return True
        if index == 0 or (index + 1) % 5 == 0:
            logger(f"手机号页: 等待短信验证码输入页出现... url={short_url(page.url)}")
        await page.wait_for_timeout(1000)
    return False


async def page_looks_like_create_password(page: Page) -> bool:
    lower_url = (page.url or "").lower()
    if "create-account/password" in lower_url or "/password" in lower_url:
        return await visible_input_count(page, r"password") > 0
    text = await body_text(page)
    low = text.lower()
    return await visible_input_count(page, r"password") > 0 and any(
        hint in low or hint in text
        for hint in ("create password", "创建密码", "设置密码")
    )


async def fill_create_password_page(page: Page, password: str, logger: Callable[[str], None]) -> None:
    locator = await first_visible(page.locator("input[type='password']"))
    if not locator:
        raise RuntimeError("创建密码页未找到密码输入框")
    await human_fill(locator, password, force_mouse=True)
    logger("手机号页: 注册密码已填入，点击继续")
    if not await click_phone_submit(page, locator):
        raise RuntimeError("创建密码页继续按钮点击失败")
    await page.wait_for_timeout(3000)


async def fill_sms_code(page: Page, code: str, logger: Callable[[str], None]) -> None:
    code_input = await find_sms_code_input(page)
    if not code_input:
        raise RuntimeError("未找到短信验证码输入框")
    logger(f"手机号页: 已找到短信验证码输入框，填入验证码 {code}")
    await human_fill(code_input, code, force_mouse=True)
    logger("手机号页: 短信验证码已填入页面")
    if not await click_phone_submit(page, code_input):
        raise RuntimeError("短信验证码提交按钮点击失败")
    logger("手机号页: 已点击验证码继续/提交按钮")
    await page.wait_for_timeout(3000)


async def wait_for_code_submit_result(page: Page, timeout: int = 12) -> tuple[str, str]:
    for _ in range(max(1, timeout * 2)):
        err = await detect_otp_error(page)
        if err:
            return "invalid", err
        if not await find_sms_code_input(page):
            return "accepted", ""
        await page.wait_for_timeout(500)
    err = await detect_otp_error(page)
    if err:
        return "invalid", err
    return "pending", ""


async def detect_otp_error(page: Page) -> str:
    low = (await body_text(page)).lower().replace("\n", " ")
    for hint in (
        "invalid code",
        "incorrect code",
        "wrong code",
        "expired code",
        "check the code and try again",
        "验证码无效",
        "验证码错误",
        "验证码已过期",
    ):
        if hint in low:
            return hint
    return ""


async def current_phone_country_code(page: Page) -> str:
    try:
        return str(
            await page.evaluate(
                r"""() => {
                    for (const node of document.querySelectorAll('button[aria-haspopup="listbox"], button, .react-aria-SelectValue')) {
                        const text = (node.innerText || node.textContent || '').trim();
                        const match = text.match(/\+(\d+)/);
                        if (match) return match[1];
                    }
                    return '';
                }"""
            )
            or ""
        )
    except Exception:
        return ""


async def maybe_visible_selector(page: Page, selector: str, timeout: int = 1000) -> Locator | None:
    deadline = asyncio.get_running_loop().time() + timeout / 1000
    while asyncio.get_running_loop().time() < deadline:
        for frame in [page.main_frame, *[frame for frame in page.frames if frame != page.main_frame]]:
            try:
                locator = frame.locator(selector).first
                if await locator.is_visible(timeout=250):
                    return locator
            except Exception:
                continue
        await page.wait_for_timeout(100)
    return None


def short_error_text(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:120] if str(exc).strip() else exc.__class__.__name__


def is_page_closed_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "target page" in text and ("closed" in text or "has been closed" in text)


def short_url(url: str, limit: int = 90) -> str:
    if len(url) <= limit:
        return url
    return url[: limit - 3] + "..."


async def body_text(page: Page) -> str:
    try:
        return await page.locator("body").inner_text(timeout=3000)
    except PlaywrightTimeoutError:
        return ""


def likely_code_page(text: str) -> bool:
    return any(
        key in text
        for key in [
            "verification code",
            "enter code",
            "one-time code",
            "otp",
            "check your email",
            "verify your email",
        ]
    )


async def is_invalid_code_page(page: Page) -> bool:
    text = await body_text(page)
    low = text.lower()
    return any(
        key in low
        for key in [
            "invalid code",
            "incorrect code",
            "code is incorrect",
            "wrong code",
            "try again",
            "代码不正确",
            "验证码不正确",
            "无效代码",
        ]
    )


def is_fatal_account_error(low: str, text: str) -> bool:
    return any(
        key in low
        for key in [
            "max_check_attempts",
            "too many attempts",
            "verification failed",
        ]
    ) or any(key in text for key in ["验证过程中出错", "糟糕，出错了", "请重试"])


async def fatal_error_message(page: Page) -> str:
    text = await body_text(page)
    if "max_check_attempts" in text:
        return "验证码检查次数已达上限(max_check_attempts)，跳过当前账号"
    return "账号注册进入不可恢复错误页，跳过当前账号"


async def is_account_picker_page(page: Page, low: str, text: str) -> bool:
    url = (page.url or "").lower()
    text_hint = any(
        marker in low or marker in text
        for marker in (
            "choose an account",
            "select an account",
            "select existing session",
            _zh(r"\u304a\u5e30\u308a\u306a\u3055\u3044"),
            _zh(r"\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u9078\u629e"),
            _zh(r"\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u9078\u629e\u3057\u3066\u304f\u3060\u3055\u3044"),
        )
    )
    if "/choose-an-account" in url:
        return True
    if not text_hint:
        return False
    try:
        state = await page.evaluate(
            """() => {
                const visible = (el) => {
                    if (!el || !el.getBoundingClientRect) return false;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                };
                const sessionCount = Array.from(document.querySelectorAll(
                    'button[name="session_id"], button[data-dd-action-name*="Select existing session" i]'
                )).filter(visible).length;
                const chooseForm = Boolean(document.querySelector('form[action*="choose-an-account"]'));
                const loginLink = Boolean(document.querySelector('a[href*="log-in-or-create-account"]'));
                const createLink = Boolean(document.querySelector('a[href*="create-account"]'));
                return { sessionCount, chooseForm, loginLink, createLink };
            }"""
        )
    except Exception:
        state = {}
    return bool(
        state.get("sessionCount")
        or state.get("chooseForm")
        or (text_hint and (state.get("loginLink") or state.get("createLink")))
    )


def is_signin_problem_page(low: str, text: str) -> bool:
    return any(
        marker in low or marker in text
        for marker in (
            "problem signing in",
            "there was a problem signing in",
            "something went wrong",
            "please wait a moment and try again",
            _zh(r"\u554f\u984c\u304c\u767a\u751f\u3057\u307e\u3057\u305f"),
            _zh(r"\u30b5\u30a4\u30f3\u30a4\u30f3\u4e2d\u306b\u554f\u984c"),
            _zh(r"\u5c11\u3057\u5f85\u3063\u3066\u304b\u3089"),
            _zh(r"\u767b\u5f55\u65f6\u51fa\u73b0\u95ee\u9898"),
            _zh(r"\u7a0d\u540e\u518d\u8bd5"),
        )
    ) and any(
        marker in low or marker in text
        for marker in (
            "back",
            "try again",
            "retry",
            _zh(r"\u623b\u308b"),
            _zh(r"\u8fd4\u56de"),
            _zh(r"\u91cd\u8bd5"),
        )
    )


async def dump_unknown_page(page: Page, index: int) -> None:
    try:
        out = Path(__file__).resolve().parents[1] / "output" / "debug"
        out.mkdir(parents=True, exist_ok=True)
        text = await body_text(page)
        (out / f"unknown_{index}.txt").write_text(
            f"URL: {page.url}\n\n{text[:4000]}",
            encoding="utf-8",
        )
        await page.screenshot(path=str(out / f"unknown_{index}.png"), full_page=True)
    except Exception:
        pass


def is_entry_page(low: str, text: str) -> bool:
    return (
        ("开始使用" in text and "登录" in text)
        or ("get started" in low and ("log in" in low or "sign up" in low))
        or ("免费注册" in text and "登录" in text)
        or ("404" in text and "找不到页面" in text and "登录" in text)
    )


async def chatgpt_logged_in_markers(page: Page, low: str, text: str) -> bool:
    if "auth/login" in (page.url or "").lower() or "auth/signup" in (page.url or "").lower():
        return False
    if "log in" in low or "sign up" in low or "登录" in text or "注册" in text:
        return False
    selectors = (
        "textarea[placeholder]",
        "textarea[data-testid]",
        "[data-testid='composer-speech-button']",
        "[data-testid='send-button']",
        "button[aria-label*='Send' i]",
        "button[aria-label*='发送']",
    )
    for selector in selectors:
        try:
            if await page.locator(selector).first.is_visible(timeout=300):
                return True
        except Exception:
            continue
    return any(
        hint in low or hint in text
        for hint in [
            "message chatgpt",
            "what can i help with",
            "准备好了，随时开始",
            "有问题，尽管问",
            "有什么可以帮忙",
        ]
    )


def is_chatgpt_success_landing(url: str, low: str, text: str) -> bool:
    value = (url or "").strip().lower()
    if "chatgpt.com" not in value:
        return False
    if any(part in value for part in ("/auth/", "/email-verification", "/about-you", "/signup", "/login")):
        return False
    path = value.split("chatgpt.com", 1)[-1].split("?", 1)[0].split("#", 1)[0] or "/"
    if path != "/" and not path.startswith(("/g/", "/c/")):
        return False
    blockers = (
        "log in",
        "login",
        "sign up",
        "create account",
        "get started",
        "verify you are human",
        "just a moment",
        "captcha",
        "cloudflare",
        "checking your browser",
    )
    return not any(item in low for item in blockers) and not any(item in text for item in ("登录", "注册"))


async def is_phone_login_page(page: Page, low: str, text: str) -> bool:
    url = (page.url or "").lower()
    phone_url = "usernamekind=phone_number" in url or "screen_hint=phone" in url
    try:
        structural_phone = bool(
            await page.evaluate(
                r"""() => {
                    const visible = (el) => {
                        if (!el || !el.getBoundingClientRect) return false;
                        const rect = el.getBoundingClientRect();
                        const style = getComputedStyle(el);
                        return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                    };
                    const phone = document.querySelector(
                        'input#phoneNumberInput, input[name="phoneNumberInput"], input[autocomplete="tel"], input[type="tel"], input[inputmode="tel"]'
                    );
                    if (visible(phone)) return true;
                    const country = document.querySelector(
                        'button[role="combobox"][aria-label*="\u96fb\u8a71\u756a\u53f7"], button[role="combobox"], select option[value="JP"][selected]'
                    );
                    return visible(country) && !!document.querySelector('input[name*="phone" i], input[id*="phone" i]');
                }"""
            )
        )
        if structural_phone:
            return True
    except Exception:
        pass
    switch_to_email = any(
        marker in low or marker in text
        for marker in (
            "continue with email",
            "continue with email address",
            _zh(r"\u7ee7\u7eed\u4f7f\u7528\u7535\u5b50\u90ae\u4ef6\u5730\u5740\u767b\u5f55"),
            _zh(r"\u7ee7\u7eed\u4f7f\u7528\u7535\u5b50\u90ae\u4ef6"),
            _zh(r"\u30e1\u30fc\u30eb\u30a2\u30c9\u30ec\u30b9\u3067\u7d9a\u884c"),
        )
    )
    switch_to_phone = any(
        marker in low or marker in text
        for marker in (
            "continue with phone",
            "continue with phone number",
            _zh(r"\u7ee7\u7eed\u4f7f\u7528\u624b\u673a\u53f7"),
            _zh(r"\u7ee7\u7eed\u4f7f\u7528\u7535\u8bdd\u53f7\u7801"),
            _zh(r"\u96fb\u8a71\u756a\u53f7\u3067\u7d9a\u884c"),
        )
    )

    phone_inputs = await visible_input_count(
        page,
        "phone|tel|mobile|"
        + _zh(r"\u7535\u8bdd\u53f7\u7801|\u624b\u673a\u53f7|\u624b\u673a|\u96fb\u8a71\u756a\u53f7|\u643a\u5e2f"),
    )
    if phone_inputs > 0 and (
        phone_url
        or switch_to_email
        or "phone number" in low
        or "mobile number" in low
        or _zh(r"\u7535\u8bdd\u53f7\u7801") in text
        or _zh(r"\u624b\u673a\u53f7") in text
        or _zh(r"\u96fb\u8a71\u756a\u53f7") in text
    ):
        return True

    email_inputs = await visible_input_count(page, r"email")
    if email_inputs > 0 or (switch_to_phone and not switch_to_email):
        return False

    username_inputs = await visible_input_count(page, r"username")
    if username_inputs > 0 and (phone_url or switch_to_email) and not switch_to_phone:
        return True

    return switch_to_email and (
        "phone number" in low
        or "mobile number" in low
        or _zh(r"\u7535\u8bdd\u53f7\u7801") in text
        or _zh(r"\u624b\u673a\u53f7") in text
        or _zh(r"\u96fb\u8a71\u756a\u53f7") in text
    )


async def visible_input_count(page: Page, attr_pattern: str) -> int:
    pattern = re.compile(attr_pattern, flags=re.I)
    inputs = await visible_locators(page.locator("input:not([type='file']), textarea"))
    count = 0
    for item in inputs:
        attrs = await input_attrs(item)
        hay = " ".join(attrs.values())
        if pattern.search(hay):
            count += 1
    return count


async def visible_code_inputs(page: Page) -> int:
    inputs = await visible_locators(page.locator("input:not([type='file'])"))
    count = 0
    for item in inputs:
        attrs = await input_attrs(item)
        hay = " ".join(attrs.values()).lower()
        if any(k in hay for k in ["code", "otp", "one-time", "verification"]):
            count += 1
    return count


async def visible_locators(locator: Locator) -> list[Locator]:
    result = []
    for index in range(await locator.count()):
        item = locator.nth(index)
        try:
            if await item.is_visible() and await item.is_enabled():
                input_type = (await item.get_attribute("type") or "").lower()
                if input_type != "file":
                    result.append(item)
        except Exception:
            continue
    return result


async def first_visible(locator: Locator) -> Locator | None:
    items = await visible_locators(locator)
    return items[0] if items else None


async def first_textbox(page: Page) -> Locator | None:
    return await first_visible(page.locator("input:not([type='file']), textarea"))


async def input_attrs(locator: Locator) -> dict[str, str]:
    keys = ["type", "name", "id", "placeholder", "autocomplete", "aria-label", "data-testid"]
    values: dict[str, str] = {}
    for key in keys:
        try:
            value = await locator.get_attribute(key)
            if value:
                values[key] = value
        except Exception:
            pass
    return values


async def human_fill(locator: Locator, value: str, force_mouse: bool = False) -> None:
    await locator.scroll_into_view_if_needed()
    if force_mouse:
        box = await locator.bounding_box()
        if box:
            await locator.page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        else:
            await locator.click(force=True)
    else:
        await locator.click(force=True)
    await locator.page.keyboard.press("Control+A")
    await locator.page.keyboard.press("Backspace")
    await locator.page.keyboard.type(str(value), delay=25)
    await locator.evaluate(
        """(el, value) => {
            if ((el.value || '').trim() !== String(value)) {
                const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (setter) setter.call(el, value);
                else el.value = value;
            }
            el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: String(value) }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""",
        str(value),
    )


async def fill_profile_stable_fields(page: Page, full_name: str, age: str, logger: Callable[[str], None] | None = None) -> bool:
    result = await page.evaluate(
        """({ fullName, age }) => {
            const visible = (el) => {
                if (!el || !el.getBoundingClientRect) return false;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const setValue = (el, value) => {
                if (!el) return;
                el.scrollIntoView({ block: 'center', inline: 'nearest' });
                const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (setter) setter.call(el, String(value));
                else el.value = String(value);
                el.setAttribute('value', String(value));
                el.focus();
                el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: String(value) }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Tab', code: 'Tab' }));
                el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Tab', code: 'Tab' }));
            };
            const firstVisible = (selectors) => {
                for (const selector of selectors) {
                    for (const el of document.querySelectorAll(selector)) {
                        if (visible(el) && !el.disabled && !el.readOnly) return el;
                    }
                }
                return null;
            };
            const nameEl = firstVisible([
                'input[name="name"]',
                'input[autocomplete="name"]',
                'input[id$="-name"]',
                'input[id*="name" i]',
                'input[aria-label*="name" i]',
                'input[placeholder*="name" i]',
                'input[aria-label*="\\u6c0f\\u540d"]',
                'input[placeholder*="\\u6c0f\\u540d"]',
                'input[aria-label*="\\u540d\\u524d"]',
                'input[placeholder*="\\u540d\\u524d"]',
            ]);
            const ageEl = firstVisible([
                'input[name="age"]',
                'input[id$="-age"]',
                'input[id*="age" i]',
                'input[type="number"][min][max]',
                'input[inputmode="numeric"][min][max]',
                'input[aria-label*="age" i]',
                'input[placeholder*="age" i]',
                'input[aria-label*="\\u5e74\\u9f62"]',
                'input[placeholder*="\\u5e74\\u9f62"]',
            ]);
            if (nameEl) setValue(nameEl, fullName);
            if (ageEl) setValue(ageEl, age);
            const ageNumber = Math.max(5, Math.min(130, parseInt(String(age || ''), 10) || 30));
            const birthday = document.querySelector('input[name="birthday"][type="hidden"]');
            if (birthday) {
                const now = new Date();
                const value = `${now.getFullYear() - ageNumber}-01-15`;
                const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(birthday, value);
                else birthday.value = value;
                birthday.setAttribute('value', value);
                birthday.dispatchEvent(new Event('input', { bubbles: true }));
                birthday.dispatchEvent(new Event('change', { bubbles: true }));
            }
            return {
                name: Boolean(nameEl),
                age: Boolean(ageEl),
                nameValue: String(nameEl?.value || ''),
                ageValue: String(ageEl?.value || ''),
                birthdayValue: String(birthday?.value || ''),
            };
        }""",
        {"fullName": full_name, "age": age},
    )
    if result.get("name") and result.get("age") and not str(result.get("nameValue") or "").strip().isdigit() and str(result.get("ageValue") or "").strip().isdigit():
        (logger or log)(f"profile stable fields filled: {result}")
        return True
    (logger or log)(f"profile stable fields not matched: {result}")
    return False


async def fill_profile_by_js(page: Page, full_name: str, age: str, logger: Callable[[str], None] | None = None) -> bool:
    result = await page.evaluate(
        """({ fullName, age }) => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const text = (node) => (node?.innerText || node?.textContent || '').replace(/\\s+/g, ' ').trim();
            const labelFor = (el) => {
                const bits = [
                    el.name, el.id, el.type, el.inputMode, el.placeholder, el.autocomplete,
                    el.getAttribute('aria-label'), el.getAttribute('data-testid')
                ];
                if (el.id) {
                    const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                    if (label) bits.push(text(label));
                }
                return bits.filter(Boolean).join(' ');
            };
            const setValue = (el, value) => {
                el.scrollIntoView({ block: 'center', inline: 'nearest' });
                const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (setter) setter.call(el, value);
                else el.value = value;
                el.setAttribute('value', value);
                el.focus();
                el.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText', data: value }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Tab', code: 'Tab' }));
                el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Tab', code: 'Tab' }));
            };
            const fields = [...document.querySelectorAll('input:not([type=file]), textarea')]
                .filter((el) => visible(el) && !el.disabled && !el.readOnly)
                .filter((el) => !/email|password|hidden|checkbox|radio|submit|button/i.test(el.type || ''));
            let nameEl = null;
            let ageEl = null;
            for (const el of fields) {
                const meta = labelFor(el);
                if (!ageEl && (/\\bage\\b|年龄|\\u5e74\\u9f62|\\u751f\\u5e74\\u6708\\u65e5|birthday|birth|year/i.test(meta) || /number|numeric|tel/i.test([el.type, el.inputMode].join(' ')))) ageEl = el;
            }
            for (const el of fields) {
                if (el === ageEl) continue;
                const meta = labelFor(el);
                if (!nameEl && /全名|姓名|名字|\\u6c0f\\u540d|\\u540d\\u524d|full\\s*name|name/i.test(meta)) nameEl = el;
            }
            if (!nameEl) nameEl = fields[0] || null;
            if (!ageEl) {
                ageEl = fields.find((el) => el !== nameEl && /number|numeric|tel/i.test([el.type, el.inputMode].join(' '))) || null;
            }
            if (!ageEl) ageEl = fields.find((el) => el !== nameEl) || null;
            for (const el of fields) {
                if (el === nameEl || el === ageEl) continue;
            }
            if (nameEl) setValue(nameEl, fullName);
            if (ageEl) setValue(ageEl, age);
            return {
                name: Boolean(nameEl),
                age: Boolean(ageEl),
                valid: Boolean(nameEl && ageEl && !/^\\d+$/.test(String(nameEl.value || '').trim()) && /^\\d+$/.test(String(ageEl.value || '').trim())),
                fields: fields.map((el) => ({ value: el.value, meta: labelFor(el).slice(0, 120) }))
            };
        }""",
        {"fullName": full_name, "age": age},
    )
    if result.get("name") and result.get("age") and result.get("valid"):
        (logger or log)("资料页已通过稳态填充写入姓名和年龄")
        return True
    (logger or log)(f"资料页稳态填充未完整命中: {result}")
    return False


async def click_profile_submit_by_js(page: Page) -> None:
    clicked = await click_submit_by_js(
        page,
        [
            "完成帐户创建", "完成账户创建", "完成帐户建立", "完成账户建立", "完成", "创建", "Continue", "Done", "Create",
            _zh(r"\u7d9a\u884c"), _zh(r"\u5b8c\u4e86"), _zh(r"\u4f5c\u6210"), _zh(r"\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u4f5c\u6210"),
        ],
    )
    if not clicked:
        await click_continue(page, profile=True)


async def click_submit_by_js(page: Page, labels: list[str]) -> bool:
    labels = list(
        dict.fromkeys(
            [
                *labels,
                _zh(r"\u7d9a\u884c"),
                _zh(r"\u6b21\u3078"),
                _zh(r"\u78ba\u8a8d"),
                _zh(r"\u9001\u4fe1"),
                _zh(r"\u30ed\u30b0\u30a4\u30f3"),
                _zh(r"\u767b\u9332"),
                _zh(r"\u4f5c\u6210"),
                _zh(r"\u5b8c\u4e86"),
            ]
        )
    )
    clicked = await page.evaluate(
        """(labels) => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const text = (el) => (el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim();
            const meta = (el) => [
                text(el),
                el?.value || '',
                el?.getAttribute?.('aria-label') || '',
                el?.getAttribute?.('title') || '',
                el?.getAttribute?.('data-testid') || '',
                el?.getAttribute?.('data-provider') || '',
                el?.outerHTML || ''
            ].join(' ').toLowerCase();
            const isSwitchEmail = (value) => /继续使用电子邮件地址登录|continue with email/i.test(value || '');
            const buttons = [...document.querySelectorAll('button, [role="button"], input[type=submit]')]
                .filter((el) => visible(el) && !el.disabled)
                .filter((el) => {
                    const value = text(el) || el.value || '';
                const hay = meta(el);
                if (/google|apple|microsoft|github|sso|oauth|social|provider/i.test(hay)) return false;
                if (isSwitchEmail(value)) return false;
                return true;
            });
            const labeled = buttons.filter((el) => (text(el) || el.value || '').trim());
            const wanted = labels.map((s) => String(s).toLowerCase());
            const exact = labeled.find((el) => {
                const value = (text(el) || el.value || '').toLowerCase();
                return wanted.some((label) => value === label);
            });
            const activate = (target) => {
                target.scrollIntoView({ block: 'center', inline: 'nearest' });
                target.focus?.();
                target.click();
                const form = target.closest?.('form') || document.querySelector('form');
                if (form && typeof form.requestSubmit === 'function') {
                    setTimeout(() => {
                        try { form.requestSubmit(target instanceof HTMLButtonElement ? target : undefined); } catch (e) {}
                    }, 50);
                }
            };
            if (exact) {
                activate(exact);
                return true;
            }
            const structuralSubmit = buttons.find((el) => {
                const type = String(el.getAttribute?.('type') || '').toLowerCase();
                const tag = String(el.tagName || '').toLowerCase();
                const form = el.closest?.('form');
                if (!form) return false;
                if (tag === 'input' && type === 'submit') return true;
                if (tag === 'button' && (type === 'submit' || type === '')) return true;
                return false;
            });
            if (structuralSubmit) {
                activate(structuralSubmit);
                return true;
            }
            const primary = labeled.find((el) => {
                const value = (text(el) || el.value || '').toLowerCase();
                return wanted.some((label) => value === label || value.includes(label));
            });
            const target = primary || labeled[labeled.length - 1];
            if (!target) return false;
            activate(target);
            return true;
        }""",
        labels,
    )
    if clicked:
        await page.keyboard.press("Enter")
        await settle(page)
        return True
    return False


async def click_email_submit(page: Page, email_input: Locator) -> bool:
    clicked = await page.evaluate(
        """(input) => {
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const label = (el) => (el?.innerText || el?.textContent || el?.value || '').replace(/\\s+/g, ' ').trim();
            const meta = (el) => [
                label(el),
                el?.getAttribute?.('aria-label') || '',
                el?.getAttribute?.('title') || '',
                el?.getAttribute?.('data-testid') || '',
                el?.getAttribute?.('data-provider') || '',
                el?.outerHTML || ''
            ].join(' ').toLowerCase();
            const isSocial = (el) => /google|apple|microsoft|github|sso|oauth|social|provider/.test(meta(el));
            const isWanted = (el) => /^(continue|next|submit|log in|sign in|sign up|create|继续|下一步|登录|注册|\u7d9a\u884c|\u6b21\u3078|\u78ba\u8a8d|\u9001\u4fe1|\u30ed\u30b0\u30a4\u30f3|\u767b\u9332)$/i.test(label(el))
                || /continue|next|继续|下一步|\u7d9a\u884c|\u6b21\u3078/.test(label(el).toLowerCase());
            const activate = (target) => {
                target.scrollIntoView({ block: 'center', inline: 'nearest' });
                target.focus?.();
                target.click();
                const form = target.closest?.('form') || input.closest?.('form');
                if (form && typeof form.requestSubmit === 'function') {
                    setTimeout(() => {
                        try { form.requestSubmit(target instanceof HTMLButtonElement ? target : undefined); } catch (e) {}
                    }, 50);
                }
            };
            const form = input.closest('form');
            const scopes = [
                form,
                input.closest('section'),
                input.closest('main'),
                input.closest('[role="main"]'),
                input.closest('div')
            ].filter(Boolean);
            for (const scope of scopes) {
                const buttons = [...scope.querySelectorAll('button, input[type=submit]')]
                    .filter((el) => visible(el) && !el.disabled && !isSocial(el));
                const wanted = buttons.find(isWanted);
                if (wanted) {
                    activate(wanted);
                    return { clicked: true, mode: 'button', label: label(wanted) };
                }
                if (form && scope === form && buttons.length === 1) {
                    activate(buttons[0]);
                    return { clicked: true, mode: 'single-form-button', label: label(buttons[0]) };
                }
            }
            input.focus();
            input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter', code: 'Enter' }));
            input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Enter', code: 'Enter' }));
            if (form && typeof form.requestSubmit === 'function') {
                try {
                    form.requestSubmit();
                    return { clicked: true, mode: 'form-enter' };
                } catch (e) {}
            }
            return { clicked: false, mode: 'not-found' };
        }""",
        await email_input.element_handle(),
    )
    if isinstance(clicked, dict) and clicked.get("clicked"):
        await page.keyboard.press("Enter")
        await settle(page)
        return True
    return False


async def click_email_submit_safe(page: Page, email_input: Locator) -> bool:
    """Submit the email form without clicking phone/SMS switch buttons."""
    handle = await email_input.element_handle()
    if not handle:
        return False
    result = await page.evaluate(
        r"""(input) => {
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const label = (el) => (el?.innerText || el?.textContent || el?.value || '').replace(/\s+/g, ' ').trim();
            const meta = (el) => [
                label(el),
                el?.getAttribute?.('aria-label') || '',
                el?.getAttribute?.('title') || '',
                el?.getAttribute?.('data-testid') || '',
                el?.getAttribute?.('data-provider') || '',
                el?.getAttribute?.('name') || '',
                el?.getAttribute?.('type') || '',
                el?.outerHTML || ''
            ].join(' ').toLowerCase();
            const hasEmailValue = () => String(input.value || '').includes('@');
            const isSocial = (el) => /google|apple|microsoft|github|sso|oauth|social|provider/.test(meta(el));
            const isPhoneSwitch = (el) => /phone|mobile|sms|tel|\u7535\u8bdd\u53f7\u7801|\u624b\u673a\u53f7|\u624b\u673a|\u96fb\u8a71\u756a\u53f7|\u643a\u5e2f/.test(meta(el));
            const isEmailSwitch = (el) => /continue with email|email address|\u7535\u5b50\u90ae\u4ef6|\u90ae\u7bb1|\u30e1\u30fc\u30eb/.test(meta(el));
            const isWanted = (el) => {
                if (isSocial(el) || isPhoneSwitch(el) || isEmailSwitch(el)) return false;
                const value = label(el).toLowerCase();
                if (/^(continue|next|submit|log in|sign in|sign up|create)$/.test(value)) return true;
                if (/^(\u7ee7\u7eed|\u4e0b\u4e00\u6b65|\u767b\u5f55|\u6ce8\u518c)$/.test(value)) return true;
                if (/^(\u7d9a\u884c|\u6b21\u3078|\u78ba\u8a8d|\u9001\u4fe1|\u30ed\u30b0\u30a4\u30f3|\u767b\u9332)$/.test(value)) return true;
                return /\b(continue|next)\b/.test(value) || /\u7ee7\u7eed|\u4e0b\u4e00\u6b65|\u7d9a\u884c|\u6b21\u3078/.test(value);
            };
            const activate = (target) => {
                target.scrollIntoView({ block: 'center', inline: 'nearest' });
                target.focus?.();
                target.click();
            };
            const form = input.closest('form');
            const scopes = [
                form,
                input.closest('[data-testid]'),
                input.closest('section'),
                input.closest('main'),
                input.closest('[role="main"]')
            ].filter(Boolean);
            for (const scope of scopes) {
                const buttons = [...scope.querySelectorAll('button, input[type=submit]')]
                    .filter((el) => visible(el) && !el.disabled);
                const wanted = buttons.find(isWanted);
                if (wanted) {
                    activate(wanted);
                    return { ok: true, mode: 'button', label: label(wanted) };
                }
                const safeSubmit = buttons.filter((el) => {
                    const type = String(el.getAttribute?.('type') || '').toLowerCase();
                    return !isSocial(el) && !isPhoneSwitch(el) && !isEmailSwitch(el) && (type === 'submit' || el.tagName === 'BUTTON');
                });
                if (form && scope === form && safeSubmit.length === 1) {
                    activate(safeSubmit[0]);
                    return { ok: true, mode: 'single-form-button', label: label(safeSubmit[0]) };
                }
            }
            input.focus();
            input.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'Enter', code: 'Enter' }));
            input.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: 'Enter', code: 'Enter' }));
            if (form && typeof form.requestSubmit === 'function' && hasEmailValue()) {
                try {
                    form.requestSubmit();
                    return { ok: true, mode: 'form-request-submit' };
                } catch (e) {}
            }
            return { ok: false, mode: 'not-found' };
        }""",
        handle,
    )
    if isinstance(result, dict) and result.get("ok"):
        await settle(page)
        return True
    return False


async def click_continue(page: Page, profile: bool = False, anchor: Locator | None = None) -> None:
    patterns = [
        re.compile(r"^(continue|next|submit|verify|log in|sign up|create|finish|done)$", re.I),
        re.compile(r"^(继续|下一步|验证|登录|注册|完成|创建)$"),
        re.compile(_zh(r"^(\u7d9a\u884c|\u6b21\u3078|\u78ba\u8a8d|\u9001\u4fe1|\u30ed\u30b0\u30a4\u30f3|\u767b\u9332|\u4f5c\u6210|\u5b8c\u4e86)$")),
    ]
    if profile:
        patterns.insert(0, re.compile(r"continue|finish|done|create", re.I))
        patterns.insert(1, re.compile(r"完成|创建|继续"))
        patterns.insert(2, re.compile(_zh(r"\u7d9a\u884c|\u4f5c\u6210|\u5b8c\u4e86|\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u4f5c\u6210")))
    if anchor:
        form_button = await find_submit_near_anchor(anchor)
        if form_button:
            await form_button.click()
            await settle(page)
            return
    for pattern in patterns:
        buttons = page.get_by_role("button", name=pattern)
        for index in range(await buttons.count()):
            button = buttons.nth(index)
            try:
                if await button.is_visible() and await button.is_enabled() and not await is_social_oauth(button):
                    await button.click()
                    await settle(page)
                    return
            except Exception:
                continue
    candidates = await visible_locators(page.locator("button, input[type='submit']"))
    candidates = [item for item in candidates if not await is_social_oauth(item)]
    if candidates:
        await candidates[0].click()
        await settle(page)
        return
    await page.keyboard.press("Enter")
    await settle(page)


async def click_by_visible_text(page: Page, label: str) -> bool:
    candidates = [
        page.locator(f"text={label}"),
        page.locator("button, a, [role='button']").filter(has_text=label),
    ]
    for locator in candidates:
        for index in range(await locator.count()):
            item = locator.nth(index)
            try:
                if await item.is_visible():
                    if await is_social_oauth(item):
                        continue
                    await item.click()
                    return True
            except Exception:
                continue
    return False


async def click_account_picker_session(page: Page, email: str, *, allow_single_fallback: bool = False) -> bool:
    clicked = await page.evaluate(
        r"""({ email, allowSingleFallback }) => {
            const wantedEmail = String(email || '').trim().toLowerCase();
            const visible = (el) => {
                if (!el || !el.getBoundingClientRect) return false;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const textOf = (el) => String(
                el?.innerText ||
                el?.textContent ||
                el?.getAttribute?.('aria-label') ||
                ''
            ).replace(/\s+/g, ' ').trim();
            const disabled = (el) => el.disabled || String(el.getAttribute?.('aria-disabled') || '').toLowerCase() === 'true';
            const buttons = Array.from(document.querySelectorAll(
                'button[name="session_id"], button[data-dd-action-name*="Select existing session" i]'
            )).filter((el) => visible(el) && !disabled(el));
            if (!buttons.length) return { clicked: false, reason: 'no_session_button' };
            let target = null;
            if (wantedEmail) {
                target = buttons.find((el) => textOf(el).toLowerCase().includes(wantedEmail));
            }
            if (!target && allowSingleFallback && buttons.length === 1) {
                target = buttons[0];
            }
            if (!target) return { clicked: false, reason: 'no_matching_session', count: buttons.length };
            target.scrollIntoView({ block: 'center', inline: 'center' });
            target.focus?.();
            target.click();
            return { clicked: true, label: textOf(target).slice(0, 160), count: buttons.length };
        }""",
        {"email": email, "allowSingleFallback": allow_single_fallback},
    )
    if isinstance(clicked, dict) and clicked.get("clicked"):
        await settle(page)
        return True
    return False


async def click_account_picker_link(page: Page, mode: str) -> bool:
    clicked = await page.evaluate(
        r"""({ mode }) => {
            const wantedMode = String(mode || '').toLowerCase();
            const visible = (el) => {
                if (!el || !el.getBoundingClientRect) return false;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const textOf = (el) => String(
                el?.innerText ||
                el?.textContent ||
                el?.getAttribute?.('aria-label') ||
                ''
            ).replace(/\s+/g, ' ').trim();
            const metaOf = (el) => [
                textOf(el),
                el?.getAttribute?.('href') || '',
                el?.getAttribute?.('aria-label') || '',
                el?.outerHTML || '',
            ].join(' ').toLowerCase();
            const nodes = Array.from(document.querySelectorAll('a[href], button, [role="button"]')).filter(visible);
            const loginPatterns = [
                /log[\s-]*in[\s-]*or[\s-]*create/i,
                /login/i,
                /log in/i,
                /\u5225\u306e\u30a2\u30ab\u30a6\u30f3\u30c8\u306b\u30ed\u30b0\u30a4\u30f3\u3059\u308b/,
                /\u5176\u4ed6\u8d26\u53f7/,
                /\u767b\u5f55/,
            ];
            const createPatterns = [
                /create[\s-]*account/i,
                /sign up/i,
                /register/i,
                /\u30a2\u30ab\u30a6\u30f3\u30c8\u3092\u4f5c\u6210\u3059\u308b/,
                /\u521b\u5efa\u8d26\u53f7/,
                /\u6ce8\u518c/,
            ];
            const patterns = wantedMode === 'login' ? loginPatterns : createPatterns;
            let target = nodes.find((el) => patterns.some((pattern) => pattern.test(metaOf(el))));
            if (!target && wantedMode === 'login') {
                target = nodes.find((el) => String(el.getAttribute?.('href') || '').includes('/log-in-or-create-account'));
            }
            if (!target && wantedMode !== 'login') {
                target = nodes.find((el) => String(el.getAttribute?.('href') || '').includes('/create-account'));
            }
            if (!target) return { clicked: false, reason: 'not_found' };
            target.scrollIntoView({ block: 'center', inline: 'center' });
            target.focus?.();
            target.click();
            return { clicked: true, label: textOf(target).slice(0, 160) };
        }""",
        {"mode": mode},
    )
    if isinstance(clicked, dict) and clicked.get("clicked"):
        await settle(page)
        return True
    return False


async def click_signin_problem_action(page: Page) -> bool:
    clicked = await page.evaluate(
        r"""() => {
            const visible = (el) => {
                if (!el || !el.getBoundingClientRect) return false;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            const textOf = (el) => String(
                el?.innerText ||
                el?.textContent ||
                el?.value ||
                el?.getAttribute?.('aria-label') ||
                ''
            ).replace(/\s+/g, ' ').trim();
            const metaOf = (el) => [
                textOf(el),
                el?.getAttribute?.('href') || '',
                el?.getAttribute?.('aria-label') || '',
                el?.outerHTML || '',
            ].join(' ').toLowerCase();
            const nodes = Array.from(document.querySelectorAll('button, a[href], [role="button"], input[type="button"], input[type="submit"]'))
                .filter((el) => visible(el) && !el.disabled);
            const patterns = [
                /^back$/i,
                /try\s*again/i,
                /retry/i,
                /continue/i,
                /\u623b\u308b/,
                /\u3082\u3046\u4e00\u5ea6/,
                /\u518d\u8a66\u884c/,
                /\u8fd4\u56de/,
                /\u91cd\u8bd5/,
                /\u7ee7\u7eed/,
            ];
            const target = nodes.find((el) => patterns.some((pattern) => pattern.test(textOf(el) || metaOf(el)))) || nodes[0];
            if (!target) return { clicked: false, reason: 'not_found' };
            target.scrollIntoView({ block: 'center', inline: 'center' });
            target.focus?.();
            target.click();
            return { clicked: true, label: textOf(target).slice(0, 160) };
        }"""
    )
    return bool(isinstance(clicked, dict) and clicked.get("clicked"))


async def cloudflare_turnstile_state(page: Page) -> dict[str, Any]:
    try:
        return dict(
            await page.evaluate(
                """() => {
                    const value = (node) => String((node && node.value) || '').trim();
                    const text = String(document.body?.innerText || '').toLowerCase();
                    const html = String(document.documentElement?.innerHTML || '').toLowerCase();
                    const title = String(document.title || '').toLowerCase();
                    const input = document.querySelector('input[name="cf-turnstile-response"]');
                    const iframeCount = document.querySelectorAll(
                        'iframe[src*="turnstile"], iframe[src*="challenge-platform"], iframe[title*="Cloudflare" i], iframe[title*="challenge" i]'
                    ).length;
                    const widgetCount = document.querySelectorAll(
                        'div.cf-turnstile, [data-sitekey], input[name="cf-turnstile-response"], script[src*="challenge-platform"], script[src*="turnstile"]'
                    ).length;
                    const visible = (el) => {
                        if (!el || !el.getBoundingClientRect) return false;
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 8 || rect.height < 8) return false;
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0.05;
                    };
                    const clickableNodes = Array.from(document.querySelectorAll(
                        'iframe[src*="turnstile"], iframe[src*="challenge-platform"], iframe[title*="Cloudflare" i], iframe[title*="challenge" i], div.cf-turnstile, [data-sitekey], [role="checkbox"], input[type="checkbox"]'
                    )).filter((node) => visible(node) && node.getAttribute('type') !== 'hidden');
                    const successNode = document.querySelector('#challenge-success-text');
                    const successVisible = visible(successNode) || text.includes('\\u691c\\u8a3c\\u306b\\u6210\\u529f') || text.includes('verification successful');
                    const challengeText = (
                        title.includes('just a moment') ||
                        title.includes('cloudflare') ||
                        text.includes('checking your browser') ||
                        text.includes('verify you are human') ||
                        text.includes('security verification') ||
                        text.includes('\\u30bb\\u30ad\\u30e5\\u30ea\\u30c6\\u30a3\\u691c\\u8a3c') ||
                        text.includes('\\u30dc\\u30c3\\u30c8\\u3067\\u306f\\u306a\\u3044') ||
                        text.includes('cloudflare') ||
                        html.includes('cf-turnstile') ||
                        html.includes('cf_chl') ||
                        html.includes('__cf_chl') ||
                        html.includes('_cf_chl_opt') ||
                        html.includes('challenge-platform') ||
                        html.includes('challenges.cloudflare.com')
                    );
                    const token = value(input);
                    return {
                        present: Boolean(input || iframeCount || widgetCount || challengeText),
                        tokenLength: token.length,
                        token,
                        iframeCount,
                        widgetCount,
                        clickableHint: clickableNodes.length > 0,
                        successVisible,
                        challengeText,
                        title,
                    };
                }"""
            )
        )
    except Exception:
        return {
            "present": False,
            "tokenLength": 0,
            "token": "",
            "iframeCount": 0,
            "widgetCount": 0,
            "clickableHint": False,
            "successVisible": False,
            "challengeText": False,
            "title": "",
        }


async def sync_cloudflare_turnstile_token(page: Page) -> int:
    try:
        return int(
            await page.evaluate(
                """() => {
                    const input = document.querySelector('input[name="cf-turnstile-response"]');
                    if (!input) return 0;
                    const token = String(
                        input.value ||
                        (window.turnstile && typeof window.turnstile.getResponse === 'function' ? window.turnstile.getResponse() : '') ||
                        ''
                    ).trim();
                    if (!token) return 0;
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                    if (setter) setter.call(input, token);
                    else input.value = token;
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                    return String(input.value || '').trim().length;
                }"""
            )
            or 0
        )
    except Exception:
        return 0


async def click_cloudflare_turnstile_widget(page: Page) -> bool:
    for frame in page.frames:
        try:
            frame_url = str(frame.url or "").lower()
        except Exception:
            frame_url = ""
        if frame != page.main_frame and not any(key in frame_url for key in ("turnstile", "cloudflare", "challenge")):
            continue
        for selector in ("input[type='checkbox']", "[role='checkbox']", "label", "button"):
            try:
                loc = frame.locator(selector).first
                if await loc.count() <= 0:
                    continue
                if not await loc.is_visible(timeout=700):
                    continue
                await loc.scroll_into_view_if_needed(timeout=700)
                await loc.click(timeout=1200, force=True)
                return True
            except Exception:
                continue
    try:
        return bool(
            await page.evaluate(
                """() => {
                    const visible = (el) => {
                        if (!el || !el.getBoundingClientRect) return false;
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
                    };
                    const dispatchClick = (target) => {
                        if (!target) return false;
                        target.scrollIntoView({ block: 'center', inline: 'center' });
                        const r = target.getBoundingClientRect();
                        const x = Math.max(1, Math.floor(r.left + Math.min(r.width / 2, 24)));
                        const y = Math.max(1, Math.floor(r.top + Math.min(r.height / 2, 24)));
                        for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                            target.dispatchEvent(new MouseEvent(type, {
                                bubbles: true,
                                cancelable: true,
                                view: window,
                                clientX: x,
                                clientY: y,
                                screenX: x,
                                screenY: y,
                            }));
                        }
                        try { target.click(); } catch {}
                        return true;
                    };
                    const selectors = [
                        'div.cf-turnstile',
                        '[data-sitekey]',
                        'iframe[src*="turnstile"]',
                        'iframe[src*="challenge-platform"]',
                        'iframe[title*="Cloudflare" i]',
                        'iframe[title*="challenge" i]',
                        '[role="checkbox"]',
                        'input[type="checkbox"]',
                    ];
                    for (const selector of selectors) {
                        const node = document.querySelector(selector);
                        if (!node) continue;
                        if (node.getAttribute('type') === 'hidden') continue;
                        const target = visible(node) ? node : node.closest('label, div.cf-turnstile, [data-sitekey]');
                        if (visible(target) && dispatchClick(target)) return true;
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


async def settle(page: Page) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    await page.wait_for_timeout(700)


async def find_submit_near_anchor(anchor: Locator) -> Locator | None:
    try:
        form = anchor.locator("xpath=ancestor::form[1]")
        if await form.count():
            buttons = await visible_locators(form.locator("button, input[type='submit']"))
            clean = [button for button in buttons if not await is_social_oauth(button)]
            if clean:
                return clean[-1]
    except Exception:
        pass
    try:
        nearby = anchor.locator(
            "xpath=ancestor::*[self::form or self::main or self::section or self::div][1]//button[not(.//*[contains(translate(., 'GOOGLEMICROSOFTAPPLE', 'googlemicrosoftapple'), 'google')])]"
        )
        buttons = await visible_locators(nearby)
        clean = [button for button in buttons if not await is_social_oauth(button)]
        if clean:
            return clean[-1]
    except Exception:
        pass
    return None


async def is_social_oauth(locator: Locator) -> bool:
    try:
        text = await locator.inner_text(timeout=1000)
    except Exception:
        text = ""
    attrs = await input_attrs(locator)
    try:
        html = await locator.evaluate("(el) => el.outerHTML || ''")
    except Exception:
        html = ""
    hay = f"{text} {' '.join(attrs.values())} {html}".lower()
    return any(key in hay for key in ["google", "apple", "microsoft", "github", "sso", "oauth", "social", "provider"])
