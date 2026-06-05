from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


KNOWN_ENV_FIELDS = [
    "REGISTER_ONLY_MODE",
    "REGISTER_ONLY_MAX_ATTEMPTS",
    "MOEMAIL_API_KEY",
    "MOEMAIL_BASE_URL",
    "MOEMAIL_DOMAIN_WHITELIST",
    "MOEMAIL_DOMAIN_MODE",
    "MOEMAIL_FIXED_DOMAIN",
    "FREE_MOEMAIL_DOMAIN_MODE",
    "FREE_MOEMAIL_FIXED_DOMAIN",
    "MOEMAIL_ENABLED",
    "MAIL_ACCOUNT_MODE",
    "MOEMAIL_CREATE_PREFIX",
    "MOEMAIL_CREATE_MODE",
    "FREE_MOEMAIL_CREATE_PREFIX",
    "FREE_MOEMAIL_CREATE_MODE",
    "AUTH_SERVER_UPLOAD",
    "SESSION_EXPORT_SERVER_UPLOAD",
    "AUTH_UPLOAD_TARGET",
    "CPA_SERVER_URL",
    "CPA_SERVER_API_KEY",
    "SUB2API_SERVER_URL",
    "SUB2API_API_KEY",
    "SUB2API_GROUP_IDS",
    "HERO_SMS_API_KEY",
    "GRIZZLY_API_KEY",
    "FIVESIM_API_KEY",
    "SMSBOWER_API_KEY",
    "SMS_VERIFICATION_NUMBER_API_KEY",
    "NEXSMS_API_KEY",
    "SMSPOOL_API_KEY",
    "CHATGPT_API_SMS_POOL_FILE",
    "CAPSOLVER_API_KEY",
    "TWOCAPTCHA_API_KEY",
    "YESCAPTCHA_API_KEY",
    "PAYPAL_CARD_REDEEM_API_KEY",
    "PAYPAL_CAPTCHA_MODE",
    "CAPTCHA_API_PROVIDER",
    "FLARESOLVERR_ENABLED",
    "FLARESOLVERR_AUTO_START",
    "FLARESOLVERR_URL",
    "FLARESOLVERR_EXECUTABLE_PATH",
    "FLARESOLVERR_TIMEOUT_SECONDS",
    "FLARESOLVERR_WAIT_SECONDS",
    "FLARESOLVERR_STARTUP_TIMEOUT_SECONDS",
    "FLARESOLVERR_DOCKER_ENABLED",
    "FLARESOLVERR_DOCKER_IMAGE",
    "FLARESOLVERR_DOCKER_NAME",
    "FLARESOLVERR_LOG_FILE",
    "FLARESOLVERR_LOG_LEVEL",
    "PAYPAL_USE_PROXY",
    "PAYPAL_REGISTER_USE_PROXY",
    "PAYPAL_REGISTER_LOCAL_PROXY_URL",
    "LOCAL_PROXY_URL",
    "PAYPAL_PROXY_FILE_US",
    "PAYPAL_PROXY_FILE_JP",
    "PAYPAL_PROXY_FILE",
    "PAYPAL_REGISTER_PROXY_FILE",
    "PAYPAL_CARD_REDEEM_ENABLED",
    "PAYPAL_CARD_REDEEM_API_URL",
    "PAYPAL_CARD_REDEEM_CODE_FIELD",
    "PAYPAL_CARD_REDEEM_TIMEOUT",
    "PAYPAL_CARD_REDEEM_MAX_AUTO_FETCH",
    "PAYPAL_CARD_CODES_FILE",
    "PAYPAL_CARD_CODES_USED_FILE",
    "PAYPAL_CARD_CODES_FAILED_FILE",
    "PAYPAL_PAYMENT_MODE",
    "PAYPAL_USE_LONG_LINK",
    "PAYPAL_CLICK_WATCHER_ENABLED",
    "PAYPAL_DIRECT_CHECKOUT_START_URL",
    "PAYPAL_SHORT_LINK_JP_PROXY_CHECK_TIMEOUT",
    "PAYPAL_CHECKOUT_METHOD",
    "PAYPAL_CHECKOUT_FALLBACK_API_URL",
    "PAYPAL_CHECKOUT_FALLBACK_API_KEY",
    "PAYPAL_CHECKOUT_FALLBACK_TIMEOUT",
    "PAYPAL_CHECKOUT_FALLBACK_PROXY",
    "PAYPAL_CHECKOUT_FALLBACK_DEFAULT_PROXY_ID",
    "PAYPAL_CHECKOUT_FALLBACK_STRIPE_PROXY_ID",
    "PAYPAL_CHECKOUT_FALLBACK_STRIPE_PROXY",
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_LINK_TYPE",
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_PAYMENT_LOCALE",
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_STRIPE_PK",
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_USER_AGENT",
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_UI_MODE",
    "PAYPAL_CHECKOUT_HOSTED_HELPER_LOCALE",
    "USE_PROXY",
    "REGISTER_LOCAL_PROXY_URL",
    "PROXY_FILE",
    "MAIL_SOURCE",
    "FLOW1_MAIL_SOURCE",
    "FLOW3_MAIL_SOURCE",
    "FREE_MAIL_SOURCE",
    "SMS_ENABLED",
    "SMS_PROVIDER",
    "FLOW1_SMS_ENABLED",
    "FLOW1_SMS_PROVIDER",
    "FLOW3_SMS_ENABLED",
    "FLOW3_SMS_PROVIDER",
    "FREE_SMS_ENABLED",
    "FREE_SMS_PROVIDER",
    "SMS_PHONE_RETRY_LIMIT",
    "SMS_PHONE_RETRY_INTERVAL",
    "HERO_SMS_SERVICE",
    "HERO_SMS_COUNTRY_TOP_N",
    "HERO_SMS_OPERATOR_THRESHOLD",
    "HERO_SMS_PROMPT_OPERATOR_SELECTION",
    "HERO_SMS_POLL_INTERVAL",
    "HERO_SMS_MAX_ATTEMPTS",
    "HERO_SMS_COUNTRY_SELECT",
    "HERO_SMS_PROMPT_COUNTRY_SELECTION",
    "GRIZZLY_SERVICE",
    "GRIZZLY_COUNTRY_TOP_N",
    "GRIZZLY_PROVIDER_THRESHOLD",
    "GRIZZLY_PROMPT_PROVIDER_SELECTION",
    "GRIZZLY_POLL_INTERVAL",
    "GRIZZLY_MAX_ATTEMPTS",
    "GRIZZLY_COUNTRY_SELECT",
    "GRIZZLY_PROMPT_COUNTRY_SELECTION",
    "FIVESIM_SERVICE",
    "FIVESIM_COUNTRY_TOP_N",
    "FIVESIM_OPERATOR_THRESHOLD",
    "FIVESIM_PROMPT_OPERATOR_SELECTION",
    "FIVESIM_POLL_INTERVAL",
    "FIVESIM_MAX_ATTEMPTS",
    "FIVESIM_COUNTRY_SELECT",
    "FIVESIM_PROMPT_COUNTRY_SELECTION",
    "SMSBOWER_SERVICE",
    "SMSBOWER_COUNTRY_TOP_N",
    "SMSBOWER_PROVIDER_THRESHOLD",
    "SMSBOWER_PROMPT_PROVIDER_SELECTION",
    "SMSBOWER_POLL_INTERVAL",
    "SMSBOWER_MAX_ATTEMPTS",
    "SMSBOWER_COUNTRY_SELECT",
    "SMSBOWER_PROMPT_COUNTRY_SELECTION",
    "SMS_VERIFICATION_NUMBER_SERVICE",
    "SMS_VERIFICATION_NUMBER_COUNTRY_TOP_N",
    "SMS_VERIFICATION_NUMBER_PROVIDER_THRESHOLD",
    "SMS_VERIFICATION_NUMBER_PROMPT_PROVIDER_SELECTION",
    "SMS_VERIFICATION_NUMBER_POLL_INTERVAL",
    "SMS_VERIFICATION_NUMBER_MAX_ATTEMPTS",
    "SMS_VERIFICATION_NUMBER_COUNTRY_SELECT",
    "SMS_VERIFICATION_NUMBER_PROMPT_COUNTRY_SELECTION",
    "NEXSMS_SERVICE",
    "NEXSMS_COUNTRY_TOP_N",
    "NEXSMS_PROVIDER_THRESHOLD",
    "NEXSMS_PROMPT_PROVIDER_SELECTION",
    "NEXSMS_POLL_INTERVAL",
    "NEXSMS_MAX_ATTEMPTS",
    "NEXSMS_COUNTRY_SELECT",
    "NEXSMS_PROMPT_COUNTRY_SELECTION",
    "SMSPOOL_SERVICE",
    "SMSPOOL_COUNTRY_TOP_N",
    "SMSPOOL_PROVIDER_THRESHOLD",
    "SMSPOOL_PROMPT_PROVIDER_SELECTION",
    "SMSPOOL_POLL_INTERVAL",
    "SMSPOOL_MAX_ATTEMPTS",
    "SMSPOOL_COUNTRY_SELECT",
    "SMSPOOL_PROMPT_COUNTRY_SELECTION",
    "CHATGPT_API_SMS_SERVICE",
    "CHATGPT_API_SMS_COUNTRY_TOP_N",
    "CHATGPT_API_SMS_PROVIDER_THRESHOLD",
    "CHATGPT_API_SMS_PROMPT_PROVIDER_SELECTION",
    "CHATGPT_API_SMS_POLL_INTERVAL",
    "CHATGPT_API_SMS_MAX_ATTEMPTS",
    "CHATGPT_API_SMS_COUNTRY_SELECT",
    "CHATGPT_API_SMS_PROMPT_COUNTRY_SELECTION",
]

DEPRECATED_ENV_KEYS = {
    "SMSBOWER_API_URL",
}


def _u(text: str) -> str:
    return text.encode("ascii").decode("unicode_escape")


ENV_FIELD_LABELS = {
    "REGISTER_ONLY_MODE": _u(r"\u4ec5\u6ce8\u518c\u5e10\u53f7\u6ce8\u518c\u65b9\u5f0f"),
    "REGISTER_ONLY_MAX_ATTEMPTS": _u(r"\u4ec5\u6ce8\u518c\u672c\u8f6e\u6700\u5927\u5c1d\u8bd5\u6b21\u6570"),
    "MOEMAIL_API_KEY": _u(r"MoeMail \u90ae\u7bb1\u670d\u52a1\u5bc6\u94a5"),
    "MOEMAIL_BASE_URL": _u(r"MoeMail \u90ae\u7bb1\u670d\u52a1\u5730\u5740"),
    "MOEMAIL_DOMAIN_WHITELIST": _u(r"MoeMail \u53ef\u7528\u57df\u540d"),
    "MOEMAIL_DOMAIN_MODE": _u(r"MoeMail \u57df\u540d\u9009\u62e9\u6a21\u5f0f"),
    "MOEMAIL_FIXED_DOMAIN": _u(r"MoeMail \u56fa\u5b9a\u57df\u540d"),
    "FREE_MOEMAIL_DOMAIN_MODE": _u(r"\u514d\u8d39\u6ce8\u518c MoeMail \u57df\u540d\u6a21\u5f0f"),
    "FREE_MOEMAIL_FIXED_DOMAIN": _u(r"\u514d\u8d39\u6ce8\u518c MoeMail \u56fa\u5b9a\u57df\u540d"),
    "MOEMAIL_ENABLED": _u(r"MoeMail \u81ea\u52a8\u8865\u53f7\u5f00\u5173"),
    "MAIL_ACCOUNT_MODE": _u(r"\u90ae\u7bb1\u8d26\u53f7\u83b7\u53d6\u6a21\u5f0f"),
    "MOEMAIL_CREATE_PREFIX": _u(r"MoeMail \u521b\u5efa\u90ae\u7bb1\u524d\u7f00"),
    "MOEMAIL_CREATE_MODE": _u(r"MoeMail \u521b\u5efa\u90ae\u7bb1\u6a21\u5f0f"),
    "FREE_MOEMAIL_CREATE_PREFIX": _u(r"\u514d\u8d39\u6ce8\u518c MoeMail \u521b\u5efa\u524d\u7f00"),
    "FREE_MOEMAIL_CREATE_MODE": _u(r"\u514d\u8d39\u6ce8\u518c MoeMail \u521b\u5efa\u6a21\u5f0f"),
    "AUTH_SERVER_UPLOAD": _u(r"\u6388\u6743\u540e\u4e0a\u4f20\u5f00\u5173"),
    "SESSION_EXPORT_SERVER_UPLOAD": _u(r"Session\u5bfc\u51fa\u4e0a\u4f20\u5f00\u5173"),
    "AUTH_UPLOAD_TARGET": _u(r"\u4e0a\u4f20\u76ee\u6807"),
    "CPA_SERVER_URL": _u(r"CPA\u670d\u52a1\u5730\u5740"),
    "CPA_SERVER_API_KEY": _u(r"CPA\u4ed3\u7ba1 Key"),
    "SUB2API_SERVER_URL": _u(r"SUB2API\u670d\u52a1\u5730\u5740"),
    "SUB2API_API_KEY": _u(r"SUB2API Key"),
    "SUB2API_GROUP_IDS": _u(r"SUB2API\u7ed1\u5b9a\u5206\u7ec4"),
    "HERO_SMS_API_KEY": _u(r"Hero \u77ed\u4fe1\u63a5\u7801\u5bc6\u94a5"),
    "GRIZZLY_API_KEY": _u(r"Grizzly \u77ed\u4fe1\u63a5\u7801\u5bc6\u94a5"),
    "FIVESIM_API_KEY": _u(r"5sim \u77ed\u4fe1\u63a5\u7801\u5bc6\u94a5"),
    "SMSBOWER_API_KEY": _u(r"SMSBower \u77ed\u4fe1\u63a5\u7801\u5bc6\u94a5"),
    "CAPSOLVER_API_KEY": _u(r"Capsolver \u9a8c\u8bc1\u7801\u5bc6\u94a5"),
    "TWOCAPTCHA_API_KEY": _u(r"2Captcha \u9a8c\u8bc1\u7801\u5bc6\u94a5"),
    "YESCAPTCHA_API_KEY": _u(r"YesCaptcha \u9a8c\u8bc1\u7801\u5bc6\u94a5"),
    "PAYPAL_CARD_REDEEM_API_KEY": _u(r"PayPal \u793c\u54c1\u5361\u5151\u6362\u5bc6\u94a5"),
    "PAYPAL_CAPTCHA_MODE": _u(r"PayPal \u9a8c\u8bc1\u7801\u6a21\u5f0f"),
    "CAPTCHA_API_PROVIDER": _u(r"\u9a8c\u8bc1\u7801\u670d\u52a1\u5546"),
    "FLARESOLVERR_ENABLED": _u(r"FlareSolverr \u5f00\u5173"),
    "FLARESOLVERR_AUTO_START": _u(r"FlareSolverr \u81ea\u52a8\u542f\u52a8"),
    "FLARESOLVERR_URL": _u(r"FlareSolverr \u63a5\u53e3\u5730\u5740"),
    "FLARESOLVERR_EXECUTABLE_PATH": _u(r"FlareSolverr \u672c\u5730\u7a0b\u5e8f\u8def\u5f84"),
    "FLARESOLVERR_TIMEOUT_SECONDS": _u(r"FlareSolverr \u8d85\u65f6\u79d2\u6570"),
    "FLARESOLVERR_WAIT_SECONDS": _u(r"FlareSolverr \u7b49\u5f85\u79d2\u6570"),
    "FLARESOLVERR_STARTUP_TIMEOUT_SECONDS": _u(r"FlareSolverr \u542f\u52a8\u7b49\u5f85\u79d2\u6570"),
    "FLARESOLVERR_DOCKER_ENABLED": _u(r"FlareSolverr Docker \u515c\u5e95"),
    "FLARESOLVERR_DOCKER_IMAGE": _u(r"FlareSolverr Docker \u955c\u50cf"),
    "FLARESOLVERR_DOCKER_NAME": _u(r"FlareSolverr Docker \u5bb9\u5668\u540d"),
    "FLARESOLVERR_LOG_FILE": _u(r"FlareSolverr \u65e5\u5fd7\u6587\u4ef6"),
    "FLARESOLVERR_LOG_LEVEL": _u(r"FlareSolverr \u65e5\u5fd7\u7ea7\u522b"),
    "PAYPAL_USE_PROXY": _u(r"PayPal \u652f\u4ed8\u4f7f\u7528\u4ee3\u7406"),
    "PAYPAL_REGISTER_USE_PROXY": _u(r"PayPal \u6ce8\u518c\u4f7f\u7528\u4ee3\u7406"),
    "PAYPAL_REGISTER_LOCAL_PROXY_URL": _u(r"PayPal \u6ce8\u518c\u672c\u5730\u4ee3\u7406\u5730\u5740"),
    "LOCAL_PROXY_URL": _u(r"\u672c\u5730\u4ee3\u7406\u5730\u5740"),
    "PAYPAL_PROXY_FILE_US": _u(r"PayPal \u7f8e\u56fd\u4ee3\u7406\u6587\u4ef6"),
    "PAYPAL_PROXY_FILE_JP": _u(r"PayPal \u65e5\u672c\u4ee3\u7406\u6587\u4ef6"),
    "PAYPAL_PROXY_FILE": _u(r"PayPal \u652f\u4ed8\u4ee3\u7406\u6587\u4ef6(\u517c\u5bb9)"),
    "PAYPAL_REGISTER_PROXY_FILE": _u(r"PayPal \u6ce8\u518c\u4ee3\u7406\u6587\u4ef6"),
    "PAYPAL_CARD_REDEEM_ENABLED": _u(r"\u542f\u7528 PayPal \u793c\u54c1\u5361\u5151\u6362"),
    "PAYPAL_CARD_REDEEM_API_URL": _u(r"PayPal \u793c\u54c1\u5361\u5151\u6362\u63a5\u53e3\u5730\u5740"),
    "PAYPAL_CARD_REDEEM_CODE_FIELD": _u(r"PayPal \u793c\u54c1\u5361\u5151\u6362\u5361\u5bc6\u5b57\u6bb5"),
    "PAYPAL_CARD_REDEEM_TIMEOUT": _u(r"PayPal \u793c\u54c1\u5361\u5151\u6362\u8d85\u65f6\u79d2\u6570"),
    "PAYPAL_CARD_REDEEM_MAX_AUTO_FETCH": _u(r"PayPal \u793c\u54c1\u5361\u6700\u5927\u81ea\u52a8\u53d6\u7801\u6570"),
    "PAYPAL_CARD_CODES_FILE": _u(r"PayPal \u793c\u54c1\u5361\u5e93\u5b58\u6587\u4ef6"),
    "PAYPAL_CARD_CODES_USED_FILE": _u(r"PayPal \u793c\u54c1\u5361\u5df2\u7528\u8bb0\u5f55\u6587\u4ef6"),
    "PAYPAL_CARD_CODES_FAILED_FILE": _u(r"PayPal \u793c\u54c1\u5361\u5931\u8d25\u8bb0\u5f55\u6587\u4ef6"),
    "PAYPAL_PAYMENT_MODE": _u(r"PayPal \u652f\u4ed8\u6a21\u5f0f"),
    "PAYPAL_USE_LONG_LINK": _u(r"PayPal \u65e5\u533a\u65e0\u5361\u4f7f\u7528\u957f\u94fe(\u517c\u5bb9)"),
    "PAYPAL_CLICK_WATCHER_ENABLED": _u(r"PayPal \u70b9\u51fb Watcher"),
    "PAYPAL_DIRECT_CHECKOUT_START_URL": _u(r"PayPal \u76f4\u63a5\u652f\u4ed8\u8d77\u59cb\u9875"),
    "PAYPAL_SHORT_LINK_JP_PROXY_CHECK_TIMEOUT": _u(r"PayPal \u77ed\u94fe JP \u4ee3\u7406\u68c0\u6d4b\u8d85\u65f6"),
    "PAYPAL_CHECKOUT_METHOD": _u(r"PayPal \u957f\u94fe\u751f\u6210\u65b9\u5f0f"),
    "PAYPAL_CHECKOUT_FALLBACK_API_URL": _u(r"PayPal \u957f\u94fe\u5916\u90e8\u4f18\u5148 API"),
    "PAYPAL_CHECKOUT_FALLBACK_API_KEY": _u(r"PayPal \u957f\u94fe\u5916\u90e8 API Key"),
    "PAYPAL_CHECKOUT_FALLBACK_TIMEOUT": _u(r"PayPal \u957f\u94fe\u5916\u90e8\u8d85\u65f6"),
    "PAYPAL_CHECKOUT_FALLBACK_PROXY": _u(r"PayPal \u957f\u94fe\u5916\u90e8\u4ee3\u7406"),
    "PAYPAL_CHECKOUT_FALLBACK_DEFAULT_PROXY_ID": _u(r"PayPal \u957f\u94fe\u5916\u90e8\u7ebf\u8def"),
    "PAYPAL_CHECKOUT_FALLBACK_STRIPE_PROXY_ID": _u(r"PayPal \u957f\u94fe Stripe \u7ebf\u8def"),
    "PAYPAL_CHECKOUT_FALLBACK_STRIPE_PROXY": _u(r"PayPal \u957f\u94fe Stripe \u4ee3\u7406"),
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_LINK_TYPE": _u(r"\u672c\u5730\u751f\u6210\u5668\u94fe\u63a5\u7c7b\u578b"),
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_PAYMENT_LOCALE": _u(r"\u672c\u5730\u751f\u6210\u5668\u652f\u4ed8\u9875\u8bed\u8a00"),
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_STRIPE_PK": _u(r"\u672c\u5730\u751f\u6210\u5668 Stripe PK"),
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_USER_AGENT": _u(r"\u672c\u5730\u751f\u6210\u5668 User-Agent"),
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_UI_MODE": _u(r"\u672c\u5730\u751f\u6210\u5668 UI \u6a21\u5f0f"),
    "PAYPAL_CHECKOUT_HOSTED_HELPER_LOCALE": _u(r"Hosted Helper \u652f\u4ed8\u9875\u8bed\u8a00"),
    "USE_PROXY": _u(r"\u6ce8\u518c\u6d41\u7a0b\u4f7f\u7528\u4ee3\u7406"),
    "REGISTER_LOCAL_PROXY_URL": _u(r"\u6ce8\u518c\u6d41\u7a0b\u672c\u5730\u4ee3\u7406\u5730\u5740"),
    "PROXY_FILE": _u(r"\u6ce8\u518c\u6d41\u7a0b\u4ee3\u7406\u6587\u4ef6"),
    "MAIL_SOURCE": _u(r"\u9ed8\u8ba4\u90ae\u7bb1\u6765\u6e90"),
    "FLOW1_MAIL_SOURCE": _u(r"\u6d41\u7a0b1\u90ae\u7bb1\u6765\u6e90"),
    "FLOW3_MAIL_SOURCE": _u(r"\u6d41\u7a0b3\u90ae\u7bb1\u6765\u6e90"),
    "FREE_MAIL_SOURCE": _u(r"\u514d\u8d39\u6ce8\u518c\u90ae\u7bb1\u6765\u6e90"),
    "SMS_ENABLED": _u(r"\u9ed8\u8ba4\u624b\u673a\u63a5\u7801\u5f00\u5173"),
    "SMS_PROVIDER": _u(r"\u9ed8\u8ba4\u63a5\u7801\u5e73\u53f0"),
    "FLOW1_SMS_ENABLED": _u(r"\u6d41\u7a0b1\u6ce8\u518c\u63a5\u7801\u5f00\u5173"),
    "FLOW1_SMS_PROVIDER": _u(r"\u6d41\u7a0b1\u6ce8\u518c\u63a5\u7801\u5e73\u53f0"),
    "FLOW3_SMS_ENABLED": _u(r"\u6d41\u7a0b3\u6388\u6743\u63a5\u7801\u5f00\u5173"),
    "FLOW3_SMS_PROVIDER": _u(r"\u6d41\u7a0b3\u6388\u6743\u63a5\u7801\u5e73\u53f0"),
    "FREE_SMS_ENABLED": _u(r"\u514d\u8d39\u6ce8\u518c\u63a5\u7801\u5f00\u5173"),
    "FREE_SMS_PROVIDER": _u(r"\u514d\u8d39\u6ce8\u518c\u63a5\u7801\u5e73\u53f0"),
    "SMS_PHONE_RETRY_LIMIT": _u(r"\u540c\u56fd\u5bb6\u6362\u53f7\u91cd\u8bd5\u6b21\u6570"),
    "SMS_PHONE_RETRY_INTERVAL": _u(r"\u540c\u56fd\u5bb6\u6362\u53f7\u95f4\u9694\u79d2\u6570"),
    "HERO_SMS_SERVICE": _u(r"Hero SMS \u6ce8\u518c\u9879\u76ee"),
    "HERO_SMS_COUNTRY_TOP_N": _u(r"Hero SMS \u663e\u793a\u5ec9\u4ef7\u56fd\u5bb6\u6570"),
    "HERO_SMS_OPERATOR_THRESHOLD": _u(r"Hero SMS \u5e93\u5b58\u4f4e\u4e8e\u6b64\u503c\u65f6\u9009\u8fd0\u8425\u5546"),
    "HERO_SMS_PROMPT_OPERATOR_SELECTION": _u(r"Hero SMS \u662f\u5426\u624b\u52a8\u9009\u8fd0\u8425\u5546"),
    "HERO_SMS_POLL_INTERVAL": _u(r"Hero SMS \u51e0\u79d2\u67e5\u4e00\u6b21\u77ed\u4fe1"),
    "HERO_SMS_MAX_ATTEMPTS": _u(r"Hero SMS \u6700\u591a\u67e5\u8be2\u6b21\u6570"),
    "HERO_SMS_COUNTRY_SELECT": _u(r"Hero SMS \u56fa\u5b9a\u4f7f\u7528\u7684\u56fd\u5bb6"),
    "HERO_SMS_PROMPT_COUNTRY_SELECTION": _u(r"Hero SMS \u662f\u5426\u624b\u52a8\u9009\u56fd\u5bb6"),
    "GRIZZLY_SERVICE": _u(r"Grizzly SMS \u6ce8\u518c\u9879\u76ee"),
    "GRIZZLY_COUNTRY_TOP_N": _u(r"Grizzly SMS \u663e\u793a\u5ec9\u4ef7\u56fd\u5bb6\u6570"),
    "GRIZZLY_PROVIDER_THRESHOLD": _u(r"Grizzly SMS \u5e93\u5b58\u4f4e\u4e8e\u6b64\u503c\u65f6\u9009\u670d\u52a1\u5546"),
    "GRIZZLY_PROMPT_PROVIDER_SELECTION": _u(r"Grizzly SMS \u662f\u5426\u624b\u52a8\u9009\u670d\u52a1\u5546"),
    "GRIZZLY_POLL_INTERVAL": _u(r"Grizzly SMS \u51e0\u79d2\u67e5\u4e00\u6b21\u77ed\u4fe1"),
    "GRIZZLY_MAX_ATTEMPTS": _u(r"Grizzly SMS \u6700\u591a\u67e5\u8be2\u6b21\u6570"),
    "GRIZZLY_COUNTRY_SELECT": _u(r"Grizzly SMS \u56fa\u5b9a\u4f7f\u7528\u7684\u56fd\u5bb6"),
    "GRIZZLY_PROMPT_COUNTRY_SELECTION": _u(r"Grizzly SMS \u662f\u5426\u624b\u52a8\u9009\u56fd\u5bb6"),
    "FIVESIM_SERVICE": _u(r"5sim \u6ce8\u518c\u9879\u76ee"),
    "FIVESIM_COUNTRY_TOP_N": _u(r"5sim \u663e\u793a\u5ec9\u4ef7\u56fd\u5bb6\u6570"),
    "FIVESIM_OPERATOR_THRESHOLD": _u(r"5sim \u5e93\u5b58\u4f4e\u4e8e\u6b64\u503c\u65f6\u9009\u8fd0\u8425\u5546"),
    "FIVESIM_PROMPT_OPERATOR_SELECTION": _u(r"5sim \u662f\u5426\u624b\u52a8\u9009\u8fd0\u8425\u5546"),
    "FIVESIM_POLL_INTERVAL": _u(r"5sim \u51e0\u79d2\u67e5\u4e00\u6b21\u77ed\u4fe1"),
    "FIVESIM_MAX_ATTEMPTS": _u(r"5sim \u6700\u591a\u67e5\u8be2\u6b21\u6570"),
    "FIVESIM_COUNTRY_SELECT": _u(r"5sim \u56fa\u5b9a\u4f7f\u7528\u7684\u56fd\u5bb6"),
    "FIVESIM_PROMPT_COUNTRY_SELECTION": _u(r"5sim \u662f\u5426\u624b\u52a8\u9009\u56fd\u5bb6"),
    "SMSBOWER_SERVICE": _u(r"SMSBower \u6ce8\u518c\u9879\u76ee"),
    "SMSBOWER_COUNTRY_TOP_N": _u(r"SMSBower \u663e\u793a\u5ec9\u4ef7\u56fd\u5bb6\u6570"),
    "SMSBOWER_PROVIDER_THRESHOLD": _u(r"SMSBower \u5e93\u5b58\u4f4e\u4e8e\u6b64\u503c\u65f6\u9009\u670d\u52a1\u5546"),
    "SMSBOWER_PROMPT_PROVIDER_SELECTION": _u(r"SMSBower \u662f\u5426\u624b\u52a8\u9009\u670d\u52a1\u5546"),
    "SMSBOWER_POLL_INTERVAL": _u(r"SMSBower \u51e0\u79d2\u67e5\u4e00\u6b21\u77ed\u4fe1"),
    "SMSBOWER_MAX_ATTEMPTS": _u(r"SMSBower \u6700\u591a\u67e5\u8be2\u6b21\u6570"),
    "SMSBOWER_COUNTRY_SELECT": _u(r"SMSBower \u56fa\u5b9a\u4f7f\u7528\u7684\u56fd\u5bb6"),
    "SMSBOWER_PROMPT_COUNTRY_SELECTION": _u(r"SMSBower \u662f\u5426\u624b\u52a8\u9009\u56fd\u5bb6"),
}


_SMS_TOOLTIP_SUFFIXES = {
    "SERVICE": _u(r"\u9009\u8981\u8d2d\u4e70\u7684\u77ed\u4fe1\u9879\u76ee\u3002\u5237\u65b0\u9009\u9879\u540e\u53ef\u76f4\u63a5\u4ece\u4e0b\u62c9\u6846\u9009 OpenAI/ChatGPT\uff1b\u4e0d\u786e\u5b9a\u65f6\u4fdd\u6301 auto/openai\u3002"),
    "COUNTRY_TOP_N": _u(r"\u4ece\u5e73\u53f0\u62a5\u4ef7\u91cc\u53ea\u663e\u793a\u6700\u4fbf\u5b9c\u7684\u524d N \u4e2a\u56fd\u5bb6\uff0c\u53ea\u5f71\u54cd\u5019\u9009\u5217\u8868\u957f\u5ea6\uff0c\u4e0d\u662f\u8d2d\u4e70\u6570\u91cf\u3002\u5efa\u8bae 10\u3002"),
    "OPERATOR_THRESHOLD": _u(r"\u5f53\u9009\u4e2d\u56fd\u5bb6\u7684\u805a\u5408\u5e93\u5b58\u4f4e\u4e8e\u8fd9\u4e2a\u6570\u503c\u65f6\uff0c\u518d\u8fdb\u5165\u8fd0\u8425\u5546\u4e8c\u6b21\u9009\u62e9\uff0c\u5c1d\u8bd5\u6311\u5e93\u5b58\u66f4\u591a\u7684\u7ebf\u8def\u3002\u5efa\u8bae 20\u3002"),
    "PROVIDER_THRESHOLD": _u(r"\u5f53\u9009\u4e2d\u56fd\u5bb6\u7684\u805a\u5408\u5e93\u5b58\u4f4e\u4e8e\u8fd9\u4e2a\u6570\u503c\u65f6\uff0c\u518d\u8fdb\u5165\u670d\u52a1\u5546\u4e8c\u6b21\u9009\u62e9\uff0c\u5c1d\u8bd5\u6311\u5e93\u5b58\u66f4\u591a\u7684\u4f9b\u5e94\u5546\u3002\u5efa\u8bae 20\u3002"),
    "PROMPT_OPERATOR_SELECTION": _u(r"\u662f\u5426\u5728\u8fd0\u884c\u65f6\u5f39\u51fa\u8fd0\u8425\u5546\u9009\u62e9\u3002true \u4f1a\u8ba9\u4f60\u624b\u52a8\u6311\uff1bfalse \u6309\u805a\u5408\u62a5\u4ef7\u81ea\u52a8\u9009\u3002"),
    "PROMPT_PROVIDER_SELECTION": _u(r"\u662f\u5426\u5728\u8fd0\u884c\u65f6\u5f39\u51fa\u670d\u52a1\u5546\u9009\u62e9\u3002true \u4f1a\u8ba9\u4f60\u624b\u52a8\u6311\uff1bfalse \u6309\u805a\u5408\u62a5\u4ef7\u81ea\u52a8\u9009\u3002"),
    "POLL_INTERVAL": _u(r"\u53d6\u5230\u624b\u673a\u53f7\u540e\uff0c\u6bcf\u9694\u591a\u5c11\u79d2\u5411\u5e73\u53f0\u67e5\u4e00\u6b21\u77ed\u4fe1\u9a8c\u8bc1\u7801\u3002\u5efa\u8bae 5 \u79d2\u3002"),
    "MAX_ATTEMPTS": _u(r"\u6700\u591a\u67e5\u8be2\u77ed\u4fe1\u9a8c\u8bc1\u7801\u7684\u6b21\u6570\u3002\u603b\u7b49\u5f85\u65f6\u95f4=\u95f4\u9694\u79d2\u6570 x \u6b21\u6570\uff0c5 x 60 \u7ea6\u7b49\u4e8e 5 \u5206\u949f\u3002"),
    "COUNTRY_SELECT": _u(r"\u56fa\u5b9a\u53ea\u4ece\u8fd9\u4e2a\u56fd\u5bb6\u8d2d\u4e70\u53f7\u7801\u3002\u5efa\u8bae\u70b9\u51fb\u201c\u5237\u65b0\u63a5\u7801\u5e73\u53f0\u9009\u9879\u201d\u540e\u4ece\u4e0b\u62c9\u6846\u9009\uff1b\u7559\u7a7a\u5219\u81ea\u52a8\u9009\u6700\u4fbf\u5b9c\u53ef\u7528\u56fd\u5bb6\u3002"),
    "PROMPT_COUNTRY_SELECTION": _u(r"\u662f\u5426\u5728\u8fd0\u884c\u65f6\u5f39\u51fa\u56fd\u5bb6\u9009\u62e9\u3002true \u624b\u52a8\u9009\uff1bfalse \u81ea\u52a8\u7528\u62a5\u4ef7\u5217\u8868\u7b2c\u4e00\u4e2a\u56fd\u5bb6\u3002"),
}


ENV_FIELD_GROUPS = {
    "REGISTER_ONLY_MODE": _u(r"\u4ec5\u6ce8\u518c"),
    "REGISTER_ONLY_MAX_ATTEMPTS": _u(r"\u4ec5\u6ce8\u518c"),
    "MOEMAIL_API_KEY": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "MOEMAIL_BASE_URL": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "MOEMAIL_DOMAIN_WHITELIST": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "MOEMAIL_DOMAIN_MODE": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "MOEMAIL_FIXED_DOMAIN": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "FREE_MOEMAIL_DOMAIN_MODE": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "FREE_MOEMAIL_FIXED_DOMAIN": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "MOEMAIL_ENABLED": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "MAIL_ACCOUNT_MODE": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "MOEMAIL_CREATE_PREFIX": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "MOEMAIL_CREATE_MODE": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "FREE_MOEMAIL_CREATE_PREFIX": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "FREE_MOEMAIL_CREATE_MODE": _u(r"\u90ae\u7bb1\u914d\u7f6e"),
    "AUTH_SERVER_UPLOAD": _u(r"\u4e0a\u4f20\u914d\u7f6e"),
    "SESSION_EXPORT_SERVER_UPLOAD": _u(r"\u4e0a\u4f20\u914d\u7f6e"),
    "AUTH_UPLOAD_TARGET": _u(r"\u4e0a\u4f20\u914d\u7f6e"),
    "CPA_SERVER_URL": _u(r"CPA \u4e0a\u4f20"),
    "CPA_SERVER_API_KEY": _u(r"CPA \u4e0a\u4f20"),
    "SUB2API_SERVER_URL": "SUB2API",
    "SUB2API_API_KEY": "SUB2API",
    "SUB2API_GROUP_IDS": "SUB2API",
    "MAIL_SOURCE": _u(r"\u90ae\u7bb1\u6765\u6e90"),
    "FLOW1_MAIL_SOURCE": _u(r"\u90ae\u7bb1\u6765\u6e90"),
    "FLOW3_MAIL_SOURCE": _u(r"\u90ae\u7bb1\u6765\u6e90"),
    "FREE_MAIL_SOURCE": _u(r"\u90ae\u7bb1\u6765\u6e90"),
    "SMS_ENABLED": _u(r"\u63a5\u7801\u603b\u5f00\u5173"),
    "SMS_PROVIDER": _u(r"\u63a5\u7801\u603b\u5f00\u5173"),
    "FLOW1_SMS_ENABLED": _u(r"\u63a5\u7801\u603b\u5f00\u5173"),
    "FLOW1_SMS_PROVIDER": _u(r"\u63a5\u7801\u603b\u5f00\u5173"),
    "FLOW3_SMS_ENABLED": _u(r"\u63a5\u7801\u603b\u5f00\u5173"),
    "FLOW3_SMS_PROVIDER": _u(r"\u63a5\u7801\u603b\u5f00\u5173"),
    "FREE_SMS_ENABLED": _u(r"\u63a5\u7801\u603b\u5f00\u5173"),
    "FREE_SMS_PROVIDER": _u(r"\u63a5\u7801\u603b\u5f00\u5173"),
    "SMS_PHONE_RETRY_LIMIT": _u(r"\u63a5\u7801\u91cd\u8bd5"),
    "SMS_PHONE_RETRY_INTERVAL": _u(r"\u63a5\u7801\u91cd\u8bd5"),
    "HERO_SMS_API_KEY": "Hero SMS",
    "HERO_SMS_SERVICE": "Hero SMS",
    "HERO_SMS_COUNTRY_TOP_N": "Hero SMS",
    "HERO_SMS_OPERATOR_THRESHOLD": "Hero SMS",
    "HERO_SMS_PROMPT_OPERATOR_SELECTION": "Hero SMS",
    "HERO_SMS_POLL_INTERVAL": "Hero SMS",
    "HERO_SMS_MAX_ATTEMPTS": "Hero SMS",
    "HERO_SMS_COUNTRY_SELECT": "Hero SMS",
    "HERO_SMS_PROMPT_COUNTRY_SELECTION": "Hero SMS",
    "GRIZZLY_API_KEY": "Grizzly SMS",
    "GRIZZLY_SERVICE": "Grizzly SMS",
    "GRIZZLY_COUNTRY_TOP_N": "Grizzly SMS",
    "GRIZZLY_PROVIDER_THRESHOLD": "Grizzly SMS",
    "GRIZZLY_PROMPT_PROVIDER_SELECTION": "Grizzly SMS",
    "GRIZZLY_POLL_INTERVAL": "Grizzly SMS",
    "GRIZZLY_MAX_ATTEMPTS": "Grizzly SMS",
    "GRIZZLY_COUNTRY_SELECT": "Grizzly SMS",
    "GRIZZLY_PROMPT_COUNTRY_SELECTION": "Grizzly SMS",
    "FIVESIM_API_KEY": "5sim",
    "FIVESIM_SERVICE": "5sim",
    "FIVESIM_COUNTRY_TOP_N": "5sim",
    "FIVESIM_OPERATOR_THRESHOLD": "5sim",
    "FIVESIM_PROMPT_OPERATOR_SELECTION": "5sim",
    "FIVESIM_POLL_INTERVAL": "5sim",
    "FIVESIM_MAX_ATTEMPTS": "5sim",
    "FIVESIM_COUNTRY_SELECT": "5sim",
    "FIVESIM_PROMPT_COUNTRY_SELECTION": "5sim",
    "SMSBOWER_API_KEY": "SMSBower",
    "SMSBOWER_SERVICE": "SMSBower",
    "SMSBOWER_COUNTRY_TOP_N": "SMSBower",
    "SMSBOWER_PROVIDER_THRESHOLD": "SMSBower",
    "SMSBOWER_PROMPT_PROVIDER_SELECTION": "SMSBower",
    "SMSBOWER_POLL_INTERVAL": "SMSBower",
    "SMSBOWER_MAX_ATTEMPTS": "SMSBower",
    "SMSBOWER_COUNTRY_SELECT": "SMSBower",
    "SMSBOWER_PROMPT_COUNTRY_SELECTION": "SMSBower",
    "CAPSOLVER_API_KEY": _u(r"\u9a8c\u8bc1\u7801"),
    "TWOCAPTCHA_API_KEY": _u(r"\u9a8c\u8bc1\u7801"),
    "YESCAPTCHA_API_KEY": _u(r"\u9a8c\u8bc1\u7801"),
    "PAYPAL_CAPTCHA_MODE": _u(r"\u9a8c\u8bc1\u7801"),
    "CAPTCHA_API_PROVIDER": _u(r"\u9a8c\u8bc1\u7801"),
    "FLARESOLVERR_ENABLED": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_AUTO_START": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_URL": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_EXECUTABLE_PATH": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_TIMEOUT_SECONDS": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_WAIT_SECONDS": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_STARTUP_TIMEOUT_SECONDS": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_DOCKER_ENABLED": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_DOCKER_IMAGE": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_DOCKER_NAME": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_LOG_FILE": _u(r"Cloudflare \u9a8c\u8bc1"),
    "FLARESOLVERR_LOG_LEVEL": _u(r"Cloudflare \u9a8c\u8bc1"),
    "PAYPAL_USE_PROXY": _u(r"\u4ee3\u7406"),
    "PAYPAL_REGISTER_USE_PROXY": _u(r"\u4ee3\u7406"),
    "PAYPAL_REGISTER_LOCAL_PROXY_URL": _u(r"\u4ee3\u7406"),
    "LOCAL_PROXY_URL": _u(r"\u4ee3\u7406"),
    "PAYPAL_PROXY_FILE_US": _u(r"\u4ee3\u7406"),
    "PAYPAL_PROXY_FILE_JP": _u(r"\u4ee3\u7406"),
    "PAYPAL_PROXY_FILE": _u(r"\u4ee3\u7406"),
    "PAYPAL_REGISTER_PROXY_FILE": _u(r"\u4ee3\u7406"),
    "USE_PROXY": _u(r"\u4ee3\u7406"),
    "REGISTER_LOCAL_PROXY_URL": _u(r"\u4ee3\u7406"),
    "PROXY_FILE": _u(r"\u4ee3\u7406"),
    "PAYPAL_CARD_REDEEM_API_KEY": _u(r"PayPal \u5361\u5bc6"),
    "PAYPAL_CARD_REDEEM_ENABLED": _u(r"PayPal \u5361\u5bc6"),
    "PAYPAL_CARD_REDEEM_API_URL": _u(r"PayPal \u5361\u5bc6"),
    "PAYPAL_CARD_REDEEM_CODE_FIELD": _u(r"PayPal \u5361\u5bc6"),
    "PAYPAL_CARD_REDEEM_TIMEOUT": _u(r"PayPal \u5361\u5bc6"),
    "PAYPAL_CARD_REDEEM_MAX_AUTO_FETCH": _u(r"PayPal \u5361\u5bc6"),
    "PAYPAL_CARD_CODES_FILE": _u(r"PayPal \u5361\u5bc6"),
    "PAYPAL_CARD_CODES_USED_FILE": _u(r"PayPal \u5361\u5bc6"),
    "PAYPAL_CARD_CODES_FAILED_FILE": _u(r"PayPal \u5361\u5bc6"),
    "PAYPAL_PAYMENT_MODE": "PayPal Plus",
    "PAYPAL_USE_LONG_LINK": "PayPal Plus",
    "PAYPAL_CLICK_WATCHER_ENABLED": "PayPal Plus",
    "PAYPAL_DIRECT_CHECKOUT_START_URL": "PayPal Plus",
    "PAYPAL_SHORT_LINK_JP_PROXY_CHECK_TIMEOUT": "PayPal Plus",
    "PAYPAL_CHECKOUT_METHOD": "PayPal Plus",
    "PAYPAL_CHECKOUT_FALLBACK_API_URL": "PayPal Plus",
    "PAYPAL_CHECKOUT_FALLBACK_API_KEY": "PayPal Plus",
    "PAYPAL_CHECKOUT_FALLBACK_TIMEOUT": "PayPal Plus",
    "PAYPAL_CHECKOUT_FALLBACK_PROXY": "PayPal Plus",
    "PAYPAL_CHECKOUT_FALLBACK_DEFAULT_PROXY_ID": "PayPal Plus",
    "PAYPAL_CHECKOUT_FALLBACK_STRIPE_PROXY_ID": "PayPal Plus",
    "PAYPAL_CHECKOUT_FALLBACK_STRIPE_PROXY": "PayPal Plus",
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_LINK_TYPE": "PayPal Plus",
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_PAYMENT_LOCALE": "PayPal Plus",
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_STRIPE_PK": "PayPal Plus",
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_USER_AGENT": "PayPal Plus",
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_UI_MODE": "PayPal Plus",
    "PAYPAL_CHECKOUT_HOSTED_HELPER_LOCALE": "PayPal Plus",
}


def _sms_tooltip(platform: str, suffix: str) -> str:
    text = _SMS_TOOLTIP_SUFFIXES.get(suffix, "")
    return f"{platform}: {text}" if text else ""


ENV_FIELD_TOOLTIPS = {
    "HERO_SMS_SERVICE": _sms_tooltip("Hero SMS", "SERVICE"),
    "HERO_SMS_COUNTRY_TOP_N": _sms_tooltip("Hero SMS", "COUNTRY_TOP_N"),
    "HERO_SMS_OPERATOR_THRESHOLD": _sms_tooltip("Hero SMS", "OPERATOR_THRESHOLD"),
    "HERO_SMS_PROMPT_OPERATOR_SELECTION": _sms_tooltip("Hero SMS", "PROMPT_OPERATOR_SELECTION"),
    "HERO_SMS_POLL_INTERVAL": _sms_tooltip("Hero SMS", "POLL_INTERVAL"),
    "HERO_SMS_MAX_ATTEMPTS": _sms_tooltip("Hero SMS", "MAX_ATTEMPTS"),
    "HERO_SMS_COUNTRY_SELECT": _sms_tooltip("Hero SMS", "COUNTRY_SELECT"),
    "HERO_SMS_PROMPT_COUNTRY_SELECTION": _sms_tooltip("Hero SMS", "PROMPT_COUNTRY_SELECTION"),
    "GRIZZLY_SERVICE": _sms_tooltip("Grizzly SMS", "SERVICE"),
    "GRIZZLY_COUNTRY_TOP_N": _sms_tooltip("Grizzly SMS", "COUNTRY_TOP_N"),
    "GRIZZLY_PROVIDER_THRESHOLD": _sms_tooltip("Grizzly SMS", "PROVIDER_THRESHOLD"),
    "GRIZZLY_PROMPT_PROVIDER_SELECTION": _sms_tooltip("Grizzly SMS", "PROMPT_PROVIDER_SELECTION"),
    "GRIZZLY_POLL_INTERVAL": _sms_tooltip("Grizzly SMS", "POLL_INTERVAL"),
    "GRIZZLY_MAX_ATTEMPTS": _sms_tooltip("Grizzly SMS", "MAX_ATTEMPTS"),
    "GRIZZLY_COUNTRY_SELECT": _sms_tooltip("Grizzly SMS", "COUNTRY_SELECT"),
    "GRIZZLY_PROMPT_COUNTRY_SELECTION": _sms_tooltip("Grizzly SMS", "PROMPT_COUNTRY_SELECTION"),
    "FIVESIM_SERVICE": _sms_tooltip("5sim", "SERVICE"),
    "FIVESIM_COUNTRY_TOP_N": _sms_tooltip("5sim", "COUNTRY_TOP_N"),
    "FIVESIM_OPERATOR_THRESHOLD": _sms_tooltip("5sim", "OPERATOR_THRESHOLD"),
    "FIVESIM_PROMPT_OPERATOR_SELECTION": _sms_tooltip("5sim", "PROMPT_OPERATOR_SELECTION"),
    "FIVESIM_POLL_INTERVAL": _sms_tooltip("5sim", "POLL_INTERVAL"),
    "FIVESIM_MAX_ATTEMPTS": _sms_tooltip("5sim", "MAX_ATTEMPTS"),
    "FIVESIM_COUNTRY_SELECT": _sms_tooltip("5sim", "COUNTRY_SELECT"),
    "FIVESIM_PROMPT_COUNTRY_SELECTION": _sms_tooltip("5sim", "PROMPT_COUNTRY_SELECTION"),
    "SMSBOWER_SERVICE": _sms_tooltip("SMSBower", "SERVICE"),
    "SMSBOWER_COUNTRY_TOP_N": _sms_tooltip("SMSBower", "COUNTRY_TOP_N"),
    "SMSBOWER_PROVIDER_THRESHOLD": _sms_tooltip("SMSBower", "PROVIDER_THRESHOLD"),
    "SMSBOWER_PROMPT_PROVIDER_SELECTION": _sms_tooltip("SMSBower", "PROMPT_PROVIDER_SELECTION"),
    "SMSBOWER_POLL_INTERVAL": _sms_tooltip("SMSBower", "POLL_INTERVAL"),
    "SMSBOWER_MAX_ATTEMPTS": _sms_tooltip("SMSBower", "MAX_ATTEMPTS"),
    "SMSBOWER_COUNTRY_SELECT": _sms_tooltip("SMSBower", "COUNTRY_SELECT"),
    "SMSBOWER_PROMPT_COUNTRY_SELECTION": _sms_tooltip("SMSBower", "PROMPT_COUNTRY_SELECTION"),
}

ENV_FIELD_TOOLTIPS.update({
    "REGISTER_ONLY_MODE": _u(r"\u4ec5\u6ce8\u518c\u8d26\u53f7\u6309\u94ae\u4f7f\u7528\u7684\u6ce8\u518c\u65b9\u5f0f\u3002email \u8868\u793a\u7528\u90ae\u7bb1\u6ce8\u518c\uff1bphone \u8868\u793a\u7528\u624b\u673a\u53f7\u6ce8\u518c\u5e76\u4f1a\u542f\u7528\u63a5\u7801\u5e73\u53f0\u3002"),
    "REGISTER_ONLY_MAX_ATTEMPTS": _u(r"\u4ec5\u6ce8\u518c\u5355\u6b21\u6279\u91cf\u6700\u591a\u5c1d\u8bd5\u591a\u5c11\u4e2a\u6d4f\u89c8\u5668\u4f1a\u8bdd\u3002\u7559\u7a7a\u65f6\u9ed8\u8ba4\u4e3a\u76ee\u6807\u6210\u529f\u6570 x 3\uff0c\u7528\u4e8e\u9632\u6b62\u540c\u4e00\u8d26\u53f7\u5931\u8d25\u540e\u65e0\u9650\u91cd\u5f00\u3002"),
    "MOEMAIL_API_KEY": _u(r"MoeMail \u63a5\u53e3\u5bc6\u94a5\u3002\u5f53\u90ae\u7bb1\u6765\u6e90\u9009 moemail\uff0c\u6216\u9700\u8981\u901a\u8fc7 MoeMail API \u81ea\u52a8\u521b\u5efa/\u8865\u5145\u90ae\u7bb1\u65f6\u4f7f\u7528\u3002"),
    "MOEMAIL_BASE_URL": _u(r"MoeMail \u670d\u52a1\u5730\u5740\u3002\u5f53\u90ae\u7bb1\u6765\u6e90\u662f moemail\uff0c\u7a0b\u5e8f\u4f1a\u7528\u8fd9\u4e2a\u5730\u5740\u8bf7\u6c42\u521b\u5efa\u90ae\u7bb1\u548c\u67e5\u8be2\u90ae\u4ef6\u9a8c\u8bc1\u7801\u3002"),
    "MOEMAIL_DOMAIN_WHITELIST": _u(r"MoeMail \u5141\u8bb8\u521b\u5efa\u90ae\u7bb1\u7684\u57df\u540d\u5217\u8868\uff0c\u591a\u4e2a\u53ef\u7528\u9017\u53f7\u5206\u9694\u3002\u81ea\u52a8\u521b\u5efa\u90ae\u7bb1\u65f6\u4f1a\u4ece\u8fd9\u91cc\u9009\u57df\u540d\u3002"),
    "MOEMAIL_DOMAIN_MODE": _u(r"MoeMail \u57df\u540d\u9009\u62e9\u7b56\u7565\u3002random \u968f\u673a\u9009\uff1bfixed \u4f7f\u7528\u56fa\u5b9a\u57df\u540d\uff1brotate \u6309\u987a\u5e8f\u8f6e\u6362\u3002"),
    "MOEMAIL_FIXED_DOMAIN": _u(r"MoeMail \u56fa\u5b9a\u57df\u540d\u3002\u53ea\u6709 MOEMAIL_DOMAIN_MODE=fixed \u65f6\u751f\u6548\uff0c\u81ea\u52a8\u521b\u5efa\u90ae\u7bb1\u4f1a\u59cb\u7ec8\u7528\u8fd9\u4e2a\u57df\u540d\u3002"),
    "FREE_MOEMAIL_DOMAIN_MODE": _u(r"Free \u6ce8\u518c\u4e13\u7528\u7684 MoeMail \u57df\u540d\u9009\u62e9\u7b56\u7565\u3002\u8fd0\u884c Free \u6ce8\u518c\u65f6\u4f18\u5148\u4f7f\u7528\u8fd9\u4e2a\u503c\uff0c\u4e0d\u586b\u5219\u56de\u9000\u5230\u901a\u7528 MoeMail \u8bbe\u7f6e\u3002"),
    "FREE_MOEMAIL_FIXED_DOMAIN": _u(r"Free \u6ce8\u518c\u4e13\u7528\u56fa\u5b9a MoeMail \u57df\u540d\u3002FREE_MOEMAIL_DOMAIN_MODE=fixed \u65f6\u751f\u6548\u3002"),
    "MOEMAIL_ENABLED": _u(r"\u662f\u5426\u542f\u7528 MoeMail \u81ea\u52a8\u8865\u53f7\u3002\u5f53\u90ae\u7bb1\u6c60\u4e0d\u591f\u65f6\uff0ctrue \u5141\u8bb8\u7a0b\u5e8f\u901a\u8fc7 MoeMail API \u81ea\u52a8\u521b\u5efa\u65b0\u90ae\u7bb1\u3002"),
    "MAIL_ACCOUNT_MODE": _u(r"\u90ae\u7bb1\u8d26\u53f7\u83b7\u53d6\u6a21\u5f0f\u3002pool \u8868\u793a\u4ece\u672c\u5730\u90ae\u7bb1\u6c60\u53d6\uff1bapi \u8868\u793a\u4f18\u5148\u901a\u8fc7 MoeMail API \u521b\u5efa/\u83b7\u53d6\u3002"),
    "MOEMAIL_CREATE_PREFIX": _u(r"MoeMail \u81ea\u52a8\u521b\u5efa\u90ae\u7bb1\u65f6\u4f7f\u7528\u7684\u524d\u7f00\u3002\u4f8b\u5982 openai \u4f1a\u751f\u6210 openai\u5f00\u5934\u7684\u90ae\u7bb1\u8d26\u53f7\u3002"),
    "MOEMAIL_CREATE_MODE": _u(r"MoeMail \u521b\u5efa\u90ae\u7bb1\u7684\u547d\u540d\u6a21\u5f0f\u3002human \u66f4\u50cf\u771f\u4eba\u540d\u79f0\uff1brandom \u66f4\u968f\u673a\u3002"),
    "FREE_MOEMAIL_CREATE_PREFIX": _u(r"Free \u6ce8\u518c\u4e13\u7528\u7684 MoeMail \u521b\u5efa\u524d\u7f00\u3002\u8fd0\u884c Free \u6ce8\u518c\u81ea\u52a8\u521b\u5efa\u90ae\u7bb1\u65f6\u4f18\u5148\u4f7f\u7528\u3002"),
    "FREE_MOEMAIL_CREATE_MODE": _u(r"Free \u6ce8\u518c\u4e13\u7528\u7684 MoeMail \u521b\u5efa\u6a21\u5f0f\u3002\u4e0d\u586b\u65f6\u56de\u9000\u5230 MOEMAIL_CREATE_MODE\u3002"),
    "AUTH_SERVER_UPLOAD": _u(r"\u6d41\u7a0b3 OAuth \u6388\u6743\u6210\u529f\u540e\u662f\u5426\u8fdc\u7a0b\u4e0a\u4f20\u8d26\u53f7\u3002true \u65f6\u6309\u201c\u4e0a\u4f20\u76ee\u6807\u201d\u628a AT/RT \u540c\u6b65\u5230 CPA\u3001SUB2API \u6216\u4e24\u8005\u3002"),
    "SESSION_EXPORT_SERVER_UPLOAD": _u(r"\u6d41\u7a0b4 Session \u672c\u5730\u5bfc\u51fa\u6210\u529f\u540e\u662f\u5426\u4e0a\u4f20\u3002\u5f00\u542f\u540e\u540c\u6837\u6309\u201c\u4e0a\u4f20\u76ee\u6807\u201d\u9009\u62e9 CPA/SUB2API\u3002"),
    "AUTH_UPLOAD_TARGET": _u(r"\u9009\u62e9\u6388\u6743\u548c Session \u5bfc\u51fa\u5b8c\u6210\u540e\u4e0a\u4f20\u5230\u54ea\u91cc\u3002cpa \u4e0a\u4f20\u5230 CPA/\u8d26\u53f7\u5e93\u670d\u52a1\uff1bsub2api \u4e0a\u4f20\u5230 SUB2API\uff1bboth \u4e24\u8fb9\u90fd\u4e0a\u4f20\uff1bnone \u4e0d\u4e0a\u4f20\u3002"),
    "CPA_SERVER_URL": _u(r"CPA \u4ed3\u7ba1\u4e2d\u5fc3\u7684\u63a5\u53e3\u5730\u5740\uff0c\u5bf9\u5e94 openai-cpa-wenfxl \u91cc cpa_mode.api_url\u3002\u4e0a\u4f20\u65f6\u7a0b\u5e8f\u4f1a\u81ea\u52a8\u4f7f\u7528 /v0/management/auth-files\u548c Authorization: Bearer Key\u3002"),
    "CPA_SERVER_API_KEY": _u(r"CPA \u4ed3\u7ba1\u4e2d\u5fc3\u7684 Key\uff0c\u5bf9\u5e94 openai-cpa-wenfxl \u91cc cpa_mode.api_token\uff0c\u4e0d\u662f SUB2API Key\u3002"),
    "SUB2API_SERVER_URL": _u(r"Sub2API \u4ed3\u7ba1\u7684\u63a5\u53e3\u5730\u5740\uff0c\u5bf9\u5e94 openai-cpa-wenfxl \u91cc sub2api_mode.api_url\u3002\u4e0a\u4f20\u65f6\u7a0b\u5e8f\u4f1a\u81ea\u52a8\u4f7f\u7528 Sub2API \u9ed8\u8ba4\u5165\u5e93\u63a5\u53e3\u3002"),
    "SUB2API_API_KEY": _u(r"Sub2API \u4ed3\u7ba1\u7684 Key\uff0c\u5bf9\u5e94 openai-cpa-wenfxl \u91cc sub2api_mode.api_key\uff0c\u4e0d\u662f CPA Key\u3002"),
    "SUB2API_GROUP_IDS": _u(r"\u4e0a\u4f20\u5230 Sub2API \u65f6\u8981\u7ed1\u5b9a\u7684\u5206\u7ec4\u3002\u70b9\u51fb\u4e0b\u62c9\u6846\u65f6\u4f1a\u901a\u8fc7 Sub2API \u63a5\u53e3\u62c9\u53d6\u5206\u7ec4\u5217\u8868\uff0c\u4fdd\u5b58\u65f6\u53ea\u5199\u5165\u5206\u7ec4 ID\uff1b\u7559\u7a7a\u5219\u4e0d\u6307\u5b9a\u5206\u7ec4\u3002"),
    "HERO_SMS_API_KEY": _u(r"Hero SMS \u63a5\u7801\u5e73\u53f0\u5bc6\u94a5\u3002\u5f53\u63a5\u7801\u5e73\u53f0\u9009 Hero SMS \u65f6\uff0c\u7528\u4e8e\u83b7\u53d6\u53ef\u7528\u56fd\u5bb6\u3001\u62a5\u4ef7\u3001\u8d2d\u53f7\u548c\u62c9\u53d6\u9a8c\u8bc1\u7801\u3002"),
    "GRIZZLY_API_KEY": _u(r"Grizzly SMS \u63a5\u7801\u5e73\u53f0\u5bc6\u94a5\u3002\u5f53\u63a5\u7801\u5e73\u53f0\u9009 Grizzly SMS \u65f6\u4f7f\u7528\u3002"),
    "FIVESIM_API_KEY": _u(r"5sim \u63a5\u7801\u5e73\u53f0 Bearer API Key\u3002\u5f53\u63a5\u7801\u5e73\u53f0\u9009 5sim \u65f6\uff0c\u7528\u4e8e\u8d2d\u4e70\u624b\u673a\u53f7\u548c\u67e5\u8be2\u77ed\u4fe1\u3002"),
    "SMSBOWER_API_KEY": _u(r"SMSBower \u63a5\u7801\u5e73\u53f0\u5bc6\u94a5\u3002\u5f53\u63a5\u7801\u5e73\u53f0\u9009 SMSBower \u65f6\u4f7f\u7528\u3002"),
    "CAPSOLVER_API_KEY": _u(r"CapSolver \u6253\u7801\u5e73\u53f0 API Key\u3002PAYPAL_CAPTCHA_MODE=api \u4e14 CAPTCHA_API_PROVIDER=capsolver \u65f6\uff0cPayPal \u6d41\u7a0b\u9047\u5230 reCAPTCHA/hCaptcha \u4f1a\u4f7f\u7528\u3002"),
    "TWOCAPTCHA_API_KEY": _u(r"2Captcha \u6253\u7801\u5e73\u53f0 API Key\u3002PAYPAL_CAPTCHA_MODE=api \u4e14 CAPTCHA_API_PROVIDER=twocaptcha \u65f6\u4f7f\u7528\u3002"),
    "YESCAPTCHA_API_KEY": _u(r"YesCaptcha \u6253\u7801\u5e73\u53f0 API Key\u3002PAYPAL_CAPTCHA_MODE=api \u4e14 CAPTCHA_API_PROVIDER=yescaptcha \u65f6\u4f7f\u7528\u3002"),
    "PAYPAL_CARD_REDEEM_API_KEY": _u(r"PayPal \u5361\u5bc6\u81ea\u52a8\u5151\u6362\u63a5\u53e3\u7684\u5bc6\u94a5\u3002\u5f53\u542f\u7528\u5361\u5bc6\u81ea\u52a8\u5151\u6362\u65f6\uff0c\u7528\u6765\u4ece\u5916\u90e8\u670d\u52a1\u83b7\u53d6\u865a\u62df\u5361\u8d44\u6599\u3002"),
    "PAYPAL_CAPTCHA_MODE": _u(r"PayPal \u6d41\u7a0b\u9047\u5230\u9a8c\u8bc1\u7801\u65f6\u7684\u5904\u7406\u65b9\u5f0f\u3002manual \u8868\u793a\u7b49\u4eba\u5de5\u5904\u7406\uff1bapi \u8868\u793a\u8c03\u7528\u6253\u7801\u5e73\u53f0\u81ea\u52a8\u89e3\u3002"),
    "CAPTCHA_API_PROVIDER": _u(r"PayPal \u9a8c\u8bc1\u7801 API \u6a21\u5f0f\u4f7f\u7528\u7684\u6253\u7801\u5e73\u53f0\u3002\u9700\u4e0e\u5bf9\u5e94\u7684 API Key \u4e00\u8d77\u914d\u7f6e\u3002"),
    "FLARESOLVERR_ENABLED": _u(r"\u662f\u5426\u5728 ChatGPT/auth.openai.com \u767b\u5f55\u72b6\u6001\u673a\u9047\u5230 Cloudflare managed challenge \u4e14 Playwright \u4f4e\u9891\u70b9\u51fb/\u7b49\u5f85\u65e0\u6cd5\u63a8\u8fdb\u65f6\uff0c\u8c03\u7528 FlareSolverr \u83b7\u53d6\u5e76\u56de\u653e cookie\u3002"),
    "FLARESOLVERR_AUTO_START": _u(r"\u5f00\u542f\u540e\uff0c\u4e3b\u9762\u677f\u542f\u52a8\u6216\u4efb\u52a1\u5f00\u59cb\u524d\u4f1a\u81ea\u52a8\u68c0\u67e5\u5e76\u542f\u52a8\u672c\u5730 FlareSolverr\u3002"),
    "FLARESOLVERR_URL": _u(r"FlareSolverr API \u5730\u5740\uff0c\u53ef\u586b http://127.0.0.1:8191 \u6216 http://127.0.0.1:8191/v1\uff0c\u7a0b\u5e8f\u4f1a\u81ea\u52a8\u8865 /v1\u3002"),
    "FLARESOLVERR_EXECUTABLE_PATH": _u(r"\u672c\u5730 FlareSolverr \u7a0b\u5e8f\u8def\u5f84\uff0c\u9ed8\u8ba4 tools/flaresolverr/FlareSolverr.exe\u3002\u5982\u679c\u4e0d\u5b58\u5728\uff0c\u4f1a\u5c1d\u8bd5\u4f7f\u7528 PATH \u91cc\u7684 flaresolverr \u6216 Docker \u515c\u5e95\u3002"),
    "FLARESOLVERR_TIMEOUT_SECONDS": _u(r"FlareSolverr request.get \u6700\u5927\u7b49\u5f85\u79d2\u6570\uff0c\u9ed8\u8ba4 120\u3002Cloudflare \u9875\u6162\u65f6\u53ef\u9002\u5f53\u589e\u5927\u3002"),
    "FLARESOLVERR_WAIT_SECONDS": _u(r"FlareSolverr \u8bf7\u6c42\u6253\u5f00\u76ee\u6807\u9875\u540e\u989d\u5916\u7b49\u5f85\u79d2\u6570\uff0c\u9ed8\u8ba4 8\u3002"),
    "FLARESOLVERR_STARTUP_TIMEOUT_SECONDS": _u(r"\u81ea\u52a8\u542f\u52a8 FlareSolverr \u540e\u7b49\u5f85 API ready \u7684\u79d2\u6570\uff0c\u9ed8\u8ba4 45\u3002"),
    "FLARESOLVERR_DOCKER_ENABLED": _u(r"\u672c\u5730 exe \u4e0d\u5b58\u5728\u6216\u542f\u52a8\u5931\u8d25\u65f6\uff0c\u662f\u5426\u5c1d\u8bd5\u7528 Docker \u542f\u52a8 ghcr.io/flaresolverr/flaresolverr\u3002"),
    "FLARESOLVERR_DOCKER_IMAGE": _u(r"Docker \u515c\u5e95\u65f6\u4f7f\u7528\u7684 FlareSolverr \u955c\u50cf\uff0c\u9ed8\u8ba4 ghcr.io/flaresolverr/flaresolverr:latest\u3002"),
    "FLARESOLVERR_DOCKER_NAME": _u(r"Docker \u515c\u5e95\u65f6\u4f7f\u7528\u7684\u5bb9\u5668\u540d\uff0c\u9ed8\u8ba4 chatgpt-plus-flaresolverr\u3002"),
    "FLARESOLVERR_LOG_FILE": _u(r"\u6258\u7ba1\u542f\u52a8\u672c\u5730 exe \u65f6\u7684\u65e5\u5fd7\u6587\u4ef6\uff0c\u9ed8\u8ba4 logs/flaresolverr.log\u3002"),
    "FLARESOLVERR_LOG_LEVEL": _u(r"\u4f20\u7ed9 FlareSolverr \u7684 LOG_LEVEL\uff0c\u9ed8\u8ba4 info\u3002"),
    "PAYPAL_USE_PROXY": _u(r"PayPal \u652f\u4ed8/\u7ed1\u5361\u6d41\u7a0b\u662f\u5426\u4f7f\u7528\u4ee3\u7406\u6c60\u3002true \u65f6\u7f8e\u56fd\u6d41\u7a0b\u8bfb\u201cPayPal \u7f8e\u56fd\u4ee3\u7406\u6587\u4ef6\u201d\uff0c\u65e5\u672c\u6d41\u7a0b\u8bfb\u201cPayPal \u65e5\u672c\u4ee3\u7406\u6587\u4ef6\u201d\uff1bfalse \u65f6\u4f7f\u7528\u672c\u5730\u4ee3\u7406\u5730\u5740\u3002"),
    "PAYPAL_REGISTER_USE_PROXY": _u(r"PayPal \u6ce8\u518c/\u751f\u6210\u957f\u94fe\u63a5\u9636\u6bb5\u662f\u5426\u4f7f\u7528\u4ee3\u7406\u6c60\u3002true \u65f6\u8bfb\u201cPayPal \u6ce8\u518c\u4ee3\u7406\u6587\u4ef6\u201d\uff1bfalse \u6216\u7559\u7a7a\u65f6\u4f7f\u7528 PayPal \u6ce8\u518c\u672c\u5730\u4ee3\u7406\u5730\u5740\uff0c\u9ed8\u8ba4 http://127.0.0.1:7897\u3002"),
    "PAYPAL_REGISTER_LOCAL_PROXY_URL": _u(r"PayPal \u6ce8\u518c/\u751f\u6210\u957f\u94fe\u63a5\u9636\u6bb5\u5173\u95ed\u4ee3\u7406\u6c60\u6216\u4ee3\u7406\u6c60\u4e3a\u7a7a\u65f6\u4f7f\u7528\u7684\u672c\u5730\u4ee3\u7406\u3002\u9ed8\u8ba4 http://127.0.0.1:7897\u3002"),
    "LOCAL_PROXY_URL": _u(r"\u4ee3\u7406\u6c60\u672a\u542f\u7528\u6216\u672a\u914d\u7f6e\u65f6\u7684\u901a\u7528\u672c\u5730\u4ee3\u7406\u3002\u4e3b\u8981\u7528\u4e8e PayPal \u652f\u4ed8/\u7ed1\u5361\u7b49\u975e\u6ce8\u518c\u515c\u5e95\u7f51\u7edc\u51fa\u53e3\uff0c\u9ed8\u8ba4 http://127.0.0.1:7987\u3002"),
    "PAYPAL_PROXY_FILE_US": _u(r"PayPal \u7f8e\u56fd\u652f\u4ed8/\u7ed1\u5361\u6d41\u7a0b\u4f7f\u7528\u7684\u4ee3\u7406\u6c60\u6587\u4ef6\u3002\u666e\u901a\u6d41\u7a0b2\u3001\u65e0\u5361\u6d41\u7a0b2\u548c\u793c\u54c1\u5361\u5151\u6362\u4f18\u5148\u8bfb\u8fd9\u4e2a\u6587\u4ef6\u3002"),
    "PAYPAL_PROXY_FILE_JP": _u(r"PayPal \u65e5\u672c\u4ee3\u7406\u6d41\u7a0b\u4f7f\u7528\u7684\u4ee3\u7406\u6c60\u6587\u4ef6\u3002\u53ea\u6709\u201c\u6d41\u7a0b2 \u65e5\u672c\u4ee3\u7406\u201d\u7cfb\u5217\u4f1a\u8bfb\u8fd9\u4e2a\u6587\u4ef6\uff0c\u4e0d\u4f1a\u518d\u6df7\u7528\u7f8e\u56fd\u4ee3\u7406\u3002"),
    "PAYPAL_PROXY_FILE": _u(r"\u5386\u53f2\u517c\u5bb9\u914d\u7f6e\u3002\u65b0\u7248\u4f18\u5148\u4f7f\u7528 PAYPAL_PROXY_FILE_US / PAYPAL_PROXY_FILE_JP\uff0c\u8fd9\u4e2a\u503c\u53ea\u4f5c\u65e7\u914d\u7f6e\u8fc7\u6e21\u3002"),
    "PAYPAL_REGISTER_PROXY_FILE": _u(r"PayPal \u6ce8\u518c/\u751f\u6210\u957f\u94fe\u63a5\u9636\u6bb5\u4f7f\u7528\u7684\u4ee3\u7406\u6c60\u6587\u4ef6\u3002PAYPAL_REGISTER_USE_PROXY=true \u65f6\u751f\u6548\uff0c\u901a\u5e38\u653e\u65e5\u672c\u4ee3\u7406\u3002"),
    "PAYPAL_CARD_REDEEM_ENABLED": _u(r"\u662f\u5426\u542f\u7528 PayPal \u5361\u5bc6\u81ea\u52a8\u5151\u6362\u3002true \u65f6\uff0c\u5361\u6c60\u4e0d\u8db3\u4f1a\u5c1d\u8bd5\u8c03\u7528\u5151\u6362\u63a5\u53e3\u8865\u5145\u865a\u62df\u5361\u3002"),
    "PAYPAL_CARD_REDEEM_API_URL": _u(r"PayPal \u5361\u5bc6\u81ea\u52a8\u5151\u6362\u7684\u63a5\u53e3\u5730\u5740\u3002\u53ea\u5728 PAYPAL_CARD_REDEEM_ENABLED=true \u65f6\u4f7f\u7528\u3002"),
    "PAYPAL_CARD_REDEEM_CODE_FIELD": _u(r"\u8c03\u7528\u5361\u5bc6\u5151\u6362\u63a5\u53e3\u65f6\uff0c\u8bf7\u6c42\u4f53\u91cc\u5361\u5bc6\u5b57\u6bb5\u7684\u540d\u79f0\u3002\u7531\u4f60\u7684\u5151\u6362\u63a5\u53e3\u8981\u6c42\u51b3\u5b9a\u3002"),
    "PAYPAL_CARD_REDEEM_TIMEOUT": _u(r"\u8c03\u7528 PayPal \u5361\u5bc6\u5151\u6362\u63a5\u53e3\u7684\u8d85\u65f6\u79d2\u6570\u3002\u63a5\u53e3\u54cd\u5e94\u6162\u65f6\u53ef\u9002\u5f53\u589e\u5927\u3002"),
    "PAYPAL_CARD_REDEEM_MAX_AUTO_FETCH": _u(r"\u5361\u6c60\u4e0d\u8db3\u65f6\uff0c\u4e00\u6b21\u6700\u591a\u81ea\u52a8\u5151\u6362\u591a\u5c11\u5f20\u5361\u3002\u7528\u4e8e\u63a7\u5236\u81ea\u52a8\u8865\u5361\u6570\u91cf\u3002"),
    "PAYPAL_CARD_CODES_FILE": _u(r"PayPal \u5361\u5bc6\u5e93\u5b58\u6587\u4ef6\u3002\u81ea\u52a8\u5151\u6362\u865a\u62df\u5361\u65f6\u4ece\u8fd9\u4e2a\u6587\u4ef6\u53d6\u672a\u7528\u5361\u5bc6\u3002"),
    "PAYPAL_CARD_CODES_USED_FILE": _u(r"PayPal \u5361\u5bc6\u5df2\u7528\u8bb0\u5f55\u6587\u4ef6\u3002\u6210\u529f\u5151\u6362\u540e\u4f1a\u628a\u5361\u5bc6\u79fb\u5230\u8fd9\u91cc\uff0c\u907f\u514d\u91cd\u590d\u4f7f\u7528\u3002"),
    "PAYPAL_CARD_CODES_FAILED_FILE": _u(r"PayPal \u5361\u5bc6\u5931\u8d25\u8bb0\u5f55\u6587\u4ef6\u3002\u5151\u6362\u5931\u8d25\u6216\u5361\u5bc6\u65e0\u6548\u65f6\u4f1a\u8bb0\u5f55\u5230\u8fd9\u91cc\u4fbf\u4e8e\u6392\u67e5\u3002"),
    "PAYPAL_PAYMENT_MODE": _u(r"PayPal \u65e5\u533a\u65e0\u5361\u652f\u4ed8\u6a21\u5f0f\u3002\u957f\u94fe\u652f\u4ed8=\u6d41\u7a0b1 \u5148\u751f\u6210\u65e5\u533a\u957f\u94fe\uff0c\u6d41\u7a0b2 \u6d88\u8d39\u957f\u94fe\u6c60\uff1b\u77ed\u94fe\u652f\u4ed8=\u6d41\u7a0b2 \u76f4\u63a5\u767b\u5f55\u8d26\u53f7\uff0c\u5168\u7a0b\u4f7f\u7528\u540c\u4e00\u65e5\u672c\u4ee3\u7406\u8fdb\u5165 ChatGPT \u5b98\u65b9\u8ba2\u9605\u5165\u53e3\u5e76\u83b7\u53d6 0 \u5143/\u514d\u8d39\u8bd5\u7528 Plus\uff0c\u540e\u7eed\u652f\u4ed8\u9875\u548c PayPal \u8d26\u5355\u5b57\u6bb5\u9009\u7f8e\u56fd\u5e76\u586b\u7f8e\u56fd\u8d44\u6599\u3002"),
    "PAYPAL_USE_LONG_LINK": _u(r"\u5386\u53f2\u517c\u5bb9\u5f00\u5173\u3002\u672a\u914d\u7f6e PAYPAL_PAYMENT_MODE \u65f6\u624d\u751f\u6548\uff1btrue=\u957f\u94fe\u652f\u4ed8\uff0cfalse=\u77ed\u94fe\u652f\u4ed8\u3002"),
    "PAYPAL_CLICK_WATCHER_ENABLED": _u(r"\u5f00\u542f\u540e\uff0c\u6d41\u7a0b2 \u6d4f\u89c8\u5668\u4f1a\u8bb0\u5f55\u6240\u6709\u70b9\u51fb\u5143\u7d20\u7684 text/selector/url \u5230 output/paypal\u6ce8\u518c/debug/click_watcher/*.jsonl\uff0c\u7528\u4e8e\u624b\u52a8\u8dd1\u4e00\u6b21\u540e\u8865\u81ea\u52a8\u5316 selector\u3002"),
    "PAYPAL_DIRECT_CHECKOUT_START_URL": _u(r"PAYPAL_PAYMENT_MODE=\u77ed\u94fe\u652f\u4ed8/short_link \u65f6\u751f\u6548\u3002\u767b\u5f55 ChatGPT \u540e\u4f1a\u6253\u5f00\u8fd9\u4e2a\u9875\u9762\u5e76\u5faa\u73af\u5bfb\u627e\u9886\u53d6\u4f18\u60e0/\u5347\u7ea7/Plus \u5165\u53e3\uff1b\u9ed8\u8ba4 https://chatgpt.com/\u3002\u5982\u679c Watcher \u8bb0\u5f55\u53d1\u73b0\u5165\u53e3\u53ea\u5728\u5176\u5b83\u9875\u9762\u51fa\u73b0\uff0c\u53ef\u76f4\u63a5\u6539\u8fd9\u4e2a URL\u3002"),
    "PAYPAL_SHORT_LINK_JP_PROXY_CHECK_TIMEOUT": _u(r"\u77ed\u94fe\u65e5\u533a\u6d41\u7a0b\u542f\u52a8\u524d\u68c0\u6d4b\u5f53\u524d\u4efb\u52a1\u4ee3\u7406\u662f\u5426\u4e3a\u65e5\u672c\u51fa\u53e3\u7684\u8d85\u65f6\u79d2\u6570\u3002\u53ea\u68c0\u6d4b\uff0c\u4e0d\u4f1a\u5728\u652f\u4ed8\u4e2d\u5207\u6362\u4ee3\u7406\uff1b\u652f\u4ed8\u9875/PayPal \u8d26\u5355\u4ecd\u4f1a\u9009\u7f8e\u56fd\u5e76\u586b\u7f8e\u56fd\u8d44\u6599\u3002"),
    "USE_PROXY": _u(r"\u6ce8\u518c\u6d41\u7a0b\u7684\u5168\u5c40\u4ee3\u7406\u5f00\u5173\u3002true \u65f6\u4ece\u6ce8\u518c\u4ee3\u7406\u6c60\u53d6\u4ee3\u7406\uff1bfalse \u6216\u7559\u7a7a\u65f6\u4f7f\u7528\u6ce8\u518c\u6d41\u7a0b\u672c\u5730\u4ee3\u7406\u5730\u5740\uff0c\u9ed8\u8ba4 http://127.0.0.1:7897\u3002"),
    "REGISTER_LOCAL_PROXY_URL": _u(r"\u6ce8\u518c\u6d41\u7a0b\u4ee3\u7406\u6c60\u672a\u542f\u7528\u6216\u4e3a\u7a7a\u65f6\u4f7f\u7528\u7684\u672c\u5730\u4ee3\u7406\u3002\u9ed8\u8ba4 http://127.0.0.1:7897\u3002"),
    "PROXY_FILE": _u(r"\u5168\u5c40\u4ee3\u7406\u6c60\u6587\u4ef6\u3002USE_PROXY=true \u65f6\u751f\u6548\uff0c\u4e00\u822c\u7528\u4e8e ChatGPT \u6ce8\u518c\u3001\u6388\u6743\u3001Free \u6ce8\u518c\u7b49\u975e PayPal \u4e13\u7528\u6d41\u7a0b\u3002"),
    "MAIL_SOURCE": _u(r"\u9ed8\u8ba4\u90ae\u7bb1\u6765\u6e90\u3002\u5f53\u67d0\u4e2a\u6d41\u7a0b\u6ca1\u6709\u5355\u72ec\u914d\u7f6e\u90ae\u7bb1\u6765\u6e90\u65f6\uff0c\u4f1a\u4f7f\u7528\u8fd9\u4e2a\u503c\u3002"),
    "FLOW1_MAIL_SOURCE": _u(r"\u6d41\u7a0b1/\u751f\u6210\u957f\u94fe\u63a5\u6ce8\u518c\u9636\u6bb5\u7684\u90ae\u7bb1\u6765\u6e90\u3002\u4e5f\u4f1a\u5f71\u54cd\u4ec5\u6ce8\u518c\u4e2d\u590d\u7528\u7684\u6ce8\u518c\u8d26\u53f7\u6d41\u7a0b\u3002"),
    "FLOW3_MAIL_SOURCE": _u(r"\u6d41\u7a0b3/OAuth \u6388\u6743\u767b\u5f55\u65f6\u7684\u90ae\u7bb1\u6765\u6e90\u3002\u7528\u4e8e\u9700\u8981\u4ece\u90ae\u7bb1\u53d6\u767b\u5f55\u9a8c\u8bc1\u7801\u7684\u573a\u666f\u3002"),
    "FREE_MAIL_SOURCE": _u(r"Free \u6ce8\u518c\u7684\u90ae\u7bb1\u6765\u6e90\u3002\u5f53\u8fd0\u884c Free \u6ce8\u518c\u6d41\u7a0b\u65f6\u4f7f\u7528\uff0c\u5f53\u524d\u4e3b\u8981\u914d\u5408 MoeMail \u81ea\u52a8\u521b\u5efa\u90ae\u7bb1\u3002"),
    "SMS_ENABLED": _u(r"\u9ed8\u8ba4\u63a5\u7801\u5f00\u5173\u3002\u5f53\u6d41\u7a0b\u6ca1\u6709\u5355\u72ec\u914d\u7f6e\u63a5\u7801\u5f00\u5173\u65f6\uff0c\u7528\u5b83\u51b3\u5b9a\u662f\u5426\u542f\u7528\u5916\u90e8\u63a5\u7801\u5e73\u53f0\u3002"),
    "SMS_PROVIDER": _u(r"\u9ed8\u8ba4\u63a5\u7801\u5e73\u53f0\u3002\u5f53\u6d41\u7a0b\u6ca1\u6709\u5355\u72ec\u6307\u5b9a\u5e73\u53f0\u65f6\uff0c\u4f7f\u7528\u8fd9\u4e2a\u503c\uff0c\u53ef\u9009 herosms/grizzly/fivesim/smsbower/sms-verification-number/nexsms/smspool/chatgpt-api\u3002"),
    "FLOW1_SMS_ENABLED": _u(r"\u6d41\u7a0b1/\u4ec5\u6ce8\u518c\u5728\u9047\u5230\u624b\u673a\u53f7\u5fc5\u586b\u9875\u65f6\u662f\u5426\u542f\u7528\u5916\u90e8\u63a5\u7801\u3002false \u65f6\u9047\u5230\u624b\u673a\u53f7\u5fc5\u586b\u4f1a\u6309\u5931\u8d25\u5904\u7406\u3002"),
    "FLOW1_SMS_PROVIDER": _u(r"\u6d41\u7a0b1/\u4ec5\u6ce8\u518c\u4f7f\u7528\u7684\u63a5\u7801\u5e73\u53f0\u3002\u542f\u7528 FLOW1_SMS_ENABLED \u540e\u751f\u6548\u3002"),
    "FLOW3_SMS_ENABLED": _u(r"\u6d41\u7a0b3/OAuth \u6388\u6743\u767b\u5f55\u9047\u5230\u624b\u673a\u9a8c\u8bc1\u65f6\u662f\u5426\u542f\u7528\u63a5\u7801\u5e73\u53f0\u3002"),
    "FLOW3_SMS_PROVIDER": _u(r"\u6d41\u7a0b3/OAuth \u6388\u6743\u4f7f\u7528\u7684\u63a5\u7801\u5e73\u53f0\u3002FLOW3_SMS_ENABLED=true \u65f6\u751f\u6548\u3002"),
    "FREE_SMS_ENABLED": _u(r"Free \u6ce8\u518c\u63a5\u7801\u5f00\u5173\u3002Free \u6ce8\u518c\u901a\u5e38\u9700\u8981\u624b\u673a\u9a8c\u8bc1\uff0c\u5173\u95ed\u540e\u9047\u5230\u624b\u673a\u9875\u65e0\u6cd5\u7ee7\u7eed\u3002"),
    "FREE_SMS_PROVIDER": _u(r"Free \u6ce8\u518c\u4f7f\u7528\u7684\u63a5\u7801\u5e73\u53f0\u3002FREE_SMS_ENABLED=true \u65f6\u751f\u6548\u3002"),
    "SMS_PHONE_RETRY_LIMIT": _u(r"\u540c\u4e00\u4e2a\u56fd\u5bb6\u4e0b\uff0c\u624b\u673a\u53f7\u586b\u5165\u540e\u9875\u9762\u4ecd\u505c\u7559\u5728\u624b\u673a\u53f7\u8868\u5355\u65f6\uff0c\u6700\u591a\u66f4\u6362\u591a\u5c11\u4e2a\u65b0\u53f7\u7801\u91cd\u8bd5\u3002\u9ed8\u8ba4 50\u3002"),
    "SMS_PHONE_RETRY_INTERVAL": _u(r"\u540c\u56fd\u5bb6\u6362\u65b0\u624b\u673a\u53f7\u4e4b\u95f4\u7684\u7b49\u5f85\u79d2\u6570\uff0c\u7528\u6765\u7ed9\u9875\u9762\u7559\u51fa\u53cd\u5e94\u65f6\u95f4\u3002\u9ed8\u8ba4 5 \u79d2\u3002"),
})

ENV_FIELD_TOOLTIPS.update({
    "PAYPAL_CHECKOUT_METHOD": _u(r"\u9009\u62e9 PayPal \u6d41\u7a0b1 \u751f\u6210\u957f\u94fe\u63a5\u65f6\u4f7f\u7528\u54ea\u4e2a\u65b9\u5f0f\u3002auto=\u6309\u987a\u5e8f\u81ea\u52a8\u515c\u5e95\uff1bexternal_api=\u5c0f\u9e21\u6bdb\u306e\u516c\u76ca\u4e91\u7aef\uff1blocal_service=\u5c0f\u9e21\u6bdb\u306e\u516c\u76ca\u672c\u5730\u670d\u52a1\uff1blocal_generator=\u672c\u5730\u652f\u4ed8\u957f\u94fe\u751f\u6210\u5668\uff1bhosted_url_helper=\u79fb\u690d hosted-url-helper \u6269\u5c55\u7b97\u6cd5\uff1bbrowser_checkout=\u672c\u9879\u76ee\u6700\u521d\u7684\u751f\u6210\u5668\u3002"),
    "PAYPAL_CHECKOUT_FALLBACK_API_URL": _u(r"\u5916\u90e8\u957f\u94fe\u751f\u6210\u5668\u7684\u57df\u540d\u6216\u5b8c\u6574 /api/checkout \u5730\u5740\u3002\u7559\u7a7a\u4f7f\u7528 https://payurl.ark2.cn/api/checkout\uff1b\u586b off \u6216 disabled \u5219\u7981\u7528\u3002\u4f18\u5148\u6309\u4f5c\u8005\u63d2\u4ef6\u534f\u8bae\u53d1\u9001 X-API-Key \u548c accessToken/paymentMethod/country/currency/requestId\uff0c\u540c\u65f6\u4fdd\u7559\u6293\u5305\u5b57\u6bb5\u517c\u5bb9\u3002\u5931\u8d25\u540e\u5148\u8d70\u672c\u5730\u540e\u7aef\u670d\u52a1\u7b97\u6cd5\uff08\u4efb\u52a1\u4ee3\u7406\u8bf7\u6c42\uff09\uff0c\u518d\u56de\u9000\u9879\u76ee\u5185\u7f6e\u751f\u6210\u65b9\u5f0f\u3002"),
    "PAYPAL_CHECKOUT_FALLBACK_API_KEY": _u(r"\u4f5c\u8005\u4e91\u7aef\u957f\u94fe API Key\uff0c\u8bf7\u6c42\u65f6\u4f7f\u7528 X-API-Key \u53d1\u9001\uff0c\u5e76\u517c\u5bb9 Authorization: Bearer\u3002"),
    "PAYPAL_CHECKOUT_FALLBACK_TIMEOUT": _u(r"\u8c03\u7528\u5916\u90e8\u957f\u94fe\u515c\u5e95 API \u7684\u8d85\u65f6\u79d2\u6570\uff0c\u9ed8\u8ba4 60\u3002"),
    "PAYPAL_CHECKOUT_FALLBACK_PROXY": _u(r"\u4f20\u7ed9\u5916\u90e8\u957f\u94fe\u751f\u6210\u5668\u7684\u81ea\u5b9a\u4e49\u51fa\u53e3\u4ee3\u7406\u5b57\u6bb5\u3002\u7559\u7a7a\u5219\u8ba9\u5916\u90e8\u670d\u52a1\u81ea\u884c\u9009\u62e9\u9ed8\u8ba4\u51fa\u53e3\u3002"),
    "PAYPAL_CHECKOUT_FALLBACK_DEFAULT_PROXY_ID": _u(r"\u4f20\u7ed9\u5916\u90e8\u957f\u94fe\u751f\u6210\u5668\u7684 default_proxy_id\u3002\u7559\u7a7a\u65f6\u6309\u6293\u5305\u4f7f\u7528 default\uff08\u65e5\u672c\u8239\u6865\u5e02\uff09\u3002"),
    "PAYPAL_CHECKOUT_FALLBACK_STRIPE_PROXY_ID": _u(r"\u4f20\u7ed9\u5916\u90e8\u957f\u94fe\u751f\u6210\u5668\u7684 stripe_proxy_id\u3002\u9ed8\u8ba4\u7559\u7a7a\uff0c\u4e0e\u6293\u5305\u4e00\u81f4\u3002"),
    "PAYPAL_CHECKOUT_FALLBACK_STRIPE_PROXY": _u(r"\u4f20\u7ed9\u5916\u90e8\u957f\u94fe\u751f\u6210\u5668\u7684 stripe_proxy\u3002\u9ed8\u8ba4\u7559\u7a7a\uff0c\u4e0e\u6293\u5305\u4e00\u81f4\u3002"),
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_LINK_TYPE": _u(r"\u672c\u5730\u652f\u4ed8\u957f\u94fe\u751f\u6210\u5668\u4f7f\u7528\u3002auto \u65f6\u65e5\u533a\u9ed8\u8ba4 paypal\uff0c\u7f8e\u533a\u9ed8\u8ba4 hosted\u3002paypal \u4f1a\u6309\u8be5\u751f\u6210\u5668\u903b\u8f91\u9501\u5b9a JP/JPY\uff0cgopay \u9501\u5b9a ID/IDR\u3002"),
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_PAYMENT_LOCALE": _u(r"\u672c\u5730\u652f\u4ed8\u957f\u94fe\u751f\u6210\u5668\u8c03\u7528 Stripe init \u65f6\u7684\u9875\u9762\u8bed\u8a00\uff0c\u9ed8\u8ba4 en\u3002"),
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_STRIPE_PK": _u(r"\u672c\u5730\u652f\u4ed8\u957f\u94fe\u751f\u6210\u5668\u7684 Stripe Publishable Key\uff0c\u7559\u7a7a\u65f6\u4f18\u5148\u7528 OpenAI \u54cd\u5e94\u91cc\u7684 key\uff0c\u518d\u7528\u9879\u76ee\u9ed8\u8ba4 key\u3002"),
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_USER_AGENT": _u(r"\u672c\u5730\u652f\u4ed8\u957f\u94fe\u751f\u6210\u5668\u8c03\u7528 Stripe init \u65f6\u4f7f\u7528\u7684 User-Agent\uff0c\u7559\u7a7a\u7528\u9ed8\u8ba4 Chrome UA\u3002"),
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_UI_MODE": _u(r"\u672c\u5730\u652f\u4ed8\u957f\u94fe\u751f\u6210\u5668\u7684 checkout_ui_mode\uff0c\u9ed8\u8ba4 hosted\u3002"),
    "PAYPAL_CHECKOUT_HOSTED_HELPER_LOCALE": _u(r"\u79fb\u690d D:\\work\\ai\\hosted-url-helper \u7684\u751f\u6210\u65b9\u5f0f\u65f6\uff0c\u5199\u5165 pay.openai.com fragment \u7684 locale\uff0c\u9ed8\u8ba4 zh\uff0c\u53ef\u586b en/zh/ja\u3002"),
})


BOOL_CHOICES = ("true", "false")
REGISTER_ONLY_MODE_CHOICES = ("email", "phone")
MAIL_SOURCE_CHOICES = ("moemail", "hotmail", "icloud_query", "domain163")
MAIL_ACCOUNT_MODE_CHOICES = ("pool", "api")
DOMAIN_MODE_CHOICES = ("random", "fixed", "rotate")
CREATE_MODE_CHOICES = ("human", "random")
SMS_PROVIDER_CHOICES = (
    "herosms",
    "grizzly",
    "fivesim",
    "smsbower",
    "sms-verification-number",
    "nexsms",
    "smspool",
    "chatgpt-api",
)
CAPTCHA_MODE_CHOICES = ("manual", "api")
CAPTCHA_PROVIDER_CHOICES = ("capsolver", "twocaptcha", "yescaptcha")
UPLOAD_TARGET_CHOICES = ("cpa", "sub2api", "both", "none")
AUTH_SCHEME_CHOICES = ("", "Bearer")
PAYPAL_CHECKOUT_METHOD_CHOICES = (
    _u(r"\u81ea\u52a8\u515c\u5e95"),
    _u(r"\u5c0f\u9e21\u6bdb\u306e\u516c\u76ca\u4e91\u7aef"),
    _u(r"\u5c0f\u9e21\u6bdb\u306e\u516c\u76ca\u672c\u5730\u670d\u52a1"),
    _u(r"\u672c\u5730 hosted-url-helper"),
    _u(r"\u672c\u5730\u652f\u4ed8\u957f\u94fe\u751f\u6210\u5668"),
    _u(r"\u672c\u9879\u76ee\u6700\u521d\u7684\u751f\u6210\u5668"),
)
PAYPAL_PAYMENT_MODE_CHOICES = (_u(r"\u957f\u94fe\u652f\u4ed8"), _u(r"\u77ed\u94fe\u652f\u4ed8"))
PAYPAL_CHECKOUT_LOCAL_GENERATOR_LINK_TYPE_CHOICES = ("auto", "hosted", "paypal", "gopay")


ENV_FIELD_CHOICES = {
    "REGISTER_ONLY_MODE": REGISTER_ONLY_MODE_CHOICES,
    "MOEMAIL_DOMAIN_MODE": DOMAIN_MODE_CHOICES,
    "FREE_MOEMAIL_DOMAIN_MODE": DOMAIN_MODE_CHOICES,
    "MOEMAIL_ENABLED": BOOL_CHOICES,
    "MAIL_ACCOUNT_MODE": MAIL_ACCOUNT_MODE_CHOICES,
    "MOEMAIL_CREATE_MODE": CREATE_MODE_CHOICES,
    "FREE_MOEMAIL_CREATE_MODE": CREATE_MODE_CHOICES,
    "AUTH_SERVER_UPLOAD": BOOL_CHOICES,
    "SESSION_EXPORT_SERVER_UPLOAD": BOOL_CHOICES,
    "AUTH_UPLOAD_TARGET": UPLOAD_TARGET_CHOICES,
    "PAYPAL_CAPTCHA_MODE": CAPTCHA_MODE_CHOICES,
    "CAPTCHA_API_PROVIDER": CAPTCHA_PROVIDER_CHOICES,
    "FLARESOLVERR_ENABLED": BOOL_CHOICES,
    "FLARESOLVERR_AUTO_START": BOOL_CHOICES,
    "FLARESOLVERR_DOCKER_ENABLED": BOOL_CHOICES,
    "PAYPAL_USE_PROXY": BOOL_CHOICES,
    "PAYPAL_REGISTER_USE_PROXY": BOOL_CHOICES,
    "PAYPAL_CARD_REDEEM_ENABLED": BOOL_CHOICES,
    "PAYPAL_PAYMENT_MODE": PAYPAL_PAYMENT_MODE_CHOICES,
    "PAYPAL_USE_LONG_LINK": BOOL_CHOICES,
    "PAYPAL_CLICK_WATCHER_ENABLED": BOOL_CHOICES,
    "PAYPAL_CHECKOUT_METHOD": PAYPAL_CHECKOUT_METHOD_CHOICES,
    "PAYPAL_CHECKOUT_LOCAL_GENERATOR_LINK_TYPE": PAYPAL_CHECKOUT_LOCAL_GENERATOR_LINK_TYPE_CHOICES,
    "USE_PROXY": BOOL_CHOICES,
    "MAIL_SOURCE": MAIL_SOURCE_CHOICES,
    "FLOW1_MAIL_SOURCE": MAIL_SOURCE_CHOICES,
    "FLOW3_MAIL_SOURCE": MAIL_SOURCE_CHOICES,
    "FREE_MAIL_SOURCE": MAIL_SOURCE_CHOICES,
    "SMS_ENABLED": BOOL_CHOICES,
    "SMS_PROVIDER": SMS_PROVIDER_CHOICES,
    "FLOW1_SMS_ENABLED": BOOL_CHOICES,
    "FLOW1_SMS_PROVIDER": SMS_PROVIDER_CHOICES,
    "FLOW3_SMS_ENABLED": BOOL_CHOICES,
    "FLOW3_SMS_PROVIDER": SMS_PROVIDER_CHOICES,
    "FREE_SMS_ENABLED": BOOL_CHOICES,
    "FREE_SMS_PROVIDER": SMS_PROVIDER_CHOICES,
    "HERO_SMS_PROMPT_OPERATOR_SELECTION": BOOL_CHOICES,
    "HERO_SMS_PROMPT_COUNTRY_SELECTION": BOOL_CHOICES,
    "GRIZZLY_PROMPT_PROVIDER_SELECTION": BOOL_CHOICES,
    "GRIZZLY_PROMPT_COUNTRY_SELECTION": BOOL_CHOICES,
    "FIVESIM_PROMPT_OPERATOR_SELECTION": BOOL_CHOICES,
    "FIVESIM_PROMPT_COUNTRY_SELECTION": BOOL_CHOICES,
    "SMSBOWER_PROMPT_PROVIDER_SELECTION": BOOL_CHOICES,
    "SMSBOWER_PROMPT_COUNTRY_SELECTION": BOOL_CHOICES,
}


_EXTRA_SMS_PLATFORM_FIELDS = {
    "SMS_VERIFICATION_NUMBER": "SMS Verification Number",
    "NEXSMS": "NexSMS",
    "SMSPOOL": "SMSPool",
    "CHATGPT_API_SMS": "ChatGPT API SMS",
}

for _prefix, _platform in _EXTRA_SMS_PLATFORM_FIELDS.items():
    _api_key = f"{_prefix}_API_KEY"
    if _api_key in KNOWN_ENV_FIELDS:
        ENV_FIELD_LABELS.setdefault(_api_key, f"{_platform} API Key")
        ENV_FIELD_GROUPS.setdefault(_api_key, _platform)
        ENV_FIELD_TOOLTIPS.setdefault(
            _api_key,
            _u(rf"{_platform} \u63a5\u7801\u5e73\u53f0 API Key\u3002\u5f53\u63a5\u7801\u5e73\u53f0\u9009 {_platform} \u65f6\u4f7f\u7528\u3002"),
        )
    for _suffix, _label_suffix, _tooltip_suffix in (
        ("SERVICE", _u(r"\u6ce8\u518c\u9879\u76ee"), "SERVICE"),
        ("COUNTRY_TOP_N", _u(r"\u663e\u793a\u5ec9\u4ef7\u56fd\u5bb6\u6570"), "COUNTRY_TOP_N"),
        ("PROVIDER_THRESHOLD", _u(r"\u5e93\u5b58\u4f4e\u4e8e\u6b64\u503c\u65f6\u9009\u670d\u52a1\u5546"), "PROVIDER_THRESHOLD"),
        ("PROMPT_PROVIDER_SELECTION", _u(r"\u662f\u5426\u624b\u52a8\u9009\u670d\u52a1\u5546"), "PROMPT_PROVIDER_SELECTION"),
        ("POLL_INTERVAL", _u(r"\u51e0\u79d2\u67e5\u4e00\u6b21\u77ed\u4fe1"), "POLL_INTERVAL"),
        ("MAX_ATTEMPTS", _u(r"\u6700\u591a\u67e5\u8be2\u6b21\u6570"), "MAX_ATTEMPTS"),
        ("COUNTRY_SELECT", _u(r"\u56fa\u5b9a\u4f7f\u7528\u7684\u56fd\u5bb6"), "COUNTRY_SELECT"),
        ("PROMPT_COUNTRY_SELECTION", _u(r"\u662f\u5426\u624b\u52a8\u9009\u56fd\u5bb6"), "PROMPT_COUNTRY_SELECTION"),
    ):
        _key = f"{_prefix}_{_suffix}"
        if _key not in KNOWN_ENV_FIELDS:
            continue
        ENV_FIELD_LABELS.setdefault(_key, f"{_platform} {_label_suffix}")
        ENV_FIELD_GROUPS.setdefault(_key, _platform)
        ENV_FIELD_TOOLTIPS.setdefault(_key, _sms_tooltip(_platform, _tooltip_suffix))
        if _suffix.startswith("PROMPT_"):
            ENV_FIELD_CHOICES.setdefault(_key, BOOL_CHOICES)

ENV_FIELD_LABELS.setdefault("CHATGPT_API_SMS_POOL_FILE", _u(r"ChatGPT API \u63a5\u7801\u53f7\u6c60\u6587\u4ef6"))
ENV_FIELD_GROUPS.setdefault("CHATGPT_API_SMS_POOL_FILE", "ChatGPT API SMS")
ENV_FIELD_TOOLTIPS.setdefault(
    "CHATGPT_API_SMS_POOL_FILE",
    _u(r"\u672c\u5730 ChatGPT API \u63a5\u7801\u53f7\u6c60\u6587\u4ef6\uff0c\u6bcf\u884c phone----url\uff0c\u6216\u624b\u673a\u53f7\u548c\u53d6\u7801 URL \u4e24\u884c\u4e00\u7ec4\u3002"),
)

@dataclass(frozen=True)
class EnvField:
    key: str
    label: str
    choices: tuple[str, ...] = ()
    tooltip: str = ""
    group: str = ""

    def __str__(self) -> str:
        return self.label

    def __eq__(self, other: object) -> bool:
        if isinstance(other, EnvField):
            return self.key == other.key
        if isinstance(other, str):
            return self.key == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.key)


def _env_key(key: object) -> str:
    raw = key.key if isinstance(key, EnvField) else key
    return str(raw).strip()


def get_known_env_fields() -> list[EnvField]:
    return [
        EnvField(
            key,
            ENV_FIELD_LABELS.get(key, key),
            ENV_FIELD_CHOICES.get(key, ()),
            ENV_FIELD_TOOLTIPS.get(key, ""),
            ENV_FIELD_GROUPS.get(key, _u(r"\u5176\u4ed6")),
        )
        for key in KNOWN_ENV_FIELDS
    ]


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in line:
        return None
    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        return None
    return key, value.strip()


def read_env(path: Path | str) -> dict[str, str]:
    file_path = Path(path)
    if not file_path.exists():
        return {}
    values: dict[str, str] = {}
    for line in file_path.read_text(encoding="utf-8-sig").splitlines():
        parsed = _parse_env_line(line)
        if parsed:
            key, value = parsed
            if key in DEPRECATED_ENV_KEYS:
                continue
            values[key] = value
    return values


def update_env(path: Path | str, updates: dict[object, str]) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    lines = file_path.read_text(encoding="utf-8-sig").splitlines() if file_path.exists() else []
    remaining = {_env_key(key): str(value) for key, value in updates.items()}
    output: list[str] = []

    for line in lines:
        parsed = _parse_env_line(line)
        if not parsed:
            output.append(line)
            continue
        key, _old_value = parsed
        if key in DEPRECATED_ENV_KEYS:
            continue
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)

    for key, value in remaining.items():
        output.append(f"{key}={value}")

    file_path.write_text("\n".join(output) + ("\n" if output else ""), encoding="utf-8")
