from modules.chatgpt_register import is_chatgpt_success_landing


def test_chatgpt_home_after_signup_is_logged_in_landing() -> None:
    assert is_chatgpt_success_landing(
        "https://chatgpt.com/",
        "what can i help with",
        "What can I help with",
    )
    assert is_chatgpt_success_landing(
        "https://www.chatgpt.com/c/abc",
        "message chatgpt",
        "Message ChatGPT",
    )


def test_auth_and_challenge_pages_are_not_logged_in_landing() -> None:
    assert not is_chatgpt_success_landing("https://chatgpt.com/auth/login", "", "")
    assert not is_chatgpt_success_landing("https://chatgpt.com/add-phone", "", "")
    assert not is_chatgpt_success_landing("https://chatgpt.com/", "verify you are human", "")
    assert not is_chatgpt_success_landing("https://auth.openai.com/about-you", "", "")
