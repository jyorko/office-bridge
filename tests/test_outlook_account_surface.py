"""Exercise the Outlook account proof against an isolated Outlook-shaped page."""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src/office_bridge/native/outlook/OutlookSession.cs").read_text(encoding="utf-8")
STATUS_SCRIPT = re.search(r'const string StatusScript = @"(.*?)";', SOURCE, re.DOTALL).group(1)
ACCOUNT = "configured.user@example.invalid"


@pytest.fixture
def page(isolated_browser):
    context = isolated_browser.new_context()
    try:
        page = context.new_page()
        page.route(
            "https://outlook.cloud.microsoft/**",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<main role='main'></main>",
            ),
        )
        page.goto("https://outlook.cloud.microsoft/")
        yield page
    finally:
        context.close()


def status_script(*, open_profile=False):
    return STATUS_SCRIPT.replace("__EXPECTED_ACCOUNT__", json.dumps(ACCOUNT)).replace(
        "__OPEN_PROFILE__", str(open_profile).lower()
    )


def test_active_outlook_profile_requires_exact_email_and_signout(page):
    page.set_content(
        f"""<header role="banner">
          <button aria-label="My account"></button>
        </header>
        <main role="main"></main>
        <div role="menu"><span>{ACCOUNT}</span><button>Sign out</button></div>"""
    )
    result = page.evaluate(status_script())
    assert result["account_menu_present"] is True
    assert result["account_verified"] is True, {
        key: result[key]
        for key in (
            "account_menu_present",
            "account_menu_email_count",
            "account_menu_matches_expected",
            "sign_out_control_count",
            "menu_surface_count",
            "app_host",
        )
    }


@pytest.mark.parametrize(
    "menu",
    [
        '<div role="menu"><span>other.user@example.invalid</span>'
        "<button>Sign out</button></div>",
        f'<div role="menu"><span>{ACCOUNT}</span><button>Switch account</button></div>',
    ],
)
def test_wrong_or_incomplete_profile_menu_does_not_verify(page, menu):
    page.set_content(
        f'<header role="banner"><button aria-label="My account"></button></header>'
        f"<main role='main'></main>{menu}"
    )
    result = page.evaluate(status_script())
    assert result["account_verified"] is False


def test_account_picker_option_does_not_verify_active_mailbox(page):
    page.set_content(
        f'<header role="banner"><button aria-label="My account"></button></header>'
        f"<main role='main'></main><div role='option'>{ACCOUNT}</div>"
    )
    result = page.evaluate(status_script())
    assert result["expected_account_option_count"] == 1
    assert result["account_verified"] is False


def test_open_shadow_root_profile_menu_is_checked_before_verifying(page):
    page.set_content(
        "<header role='banner'><span id='profile-host'></span></header>" "<main role='main'></main>"
    )
    page.evaluate(
        """({account}) => {
          const root = document.querySelector('#profile-host').attachShadow({mode:'open'});
          const button = document.createElement('button');
          button.setAttribute('aria-label', 'Account');
          button.addEventListener('click', () => {
            const menu = document.createElement('div');
            menu.setAttribute('role', 'menu');
            menu.innerHTML = `<span>${account}</span><button>Sign out</button>`;
            root.append(menu);
          });
          root.append(button);
        }""",
        {"account": ACCOUNT},
    )
    opened = page.evaluate(status_script(open_profile=True))
    assert opened["account_verified"] is False
    page.wait_for_timeout(25)
    verified = page.evaluate(status_script())
    assert verified["account_menu_present"] is True
    assert verified["account_verified"] is True, verified


def test_top_right_round_profile_image_is_the_only_menu_trigger_clicked(page):
    page.set_viewport_size({"width": 1200, "height": 900})
    page.set_content(
        """<main role="main"></main>
        <button style="position:absolute;left:1080px;top:16px;width:40px;height:40px">
          <img alt="Profile photo" style="width:32px;height:32px;border-radius:50%">
        </button>"""
    )
    page.evaluate(
        """({account}) => {
          document.querySelector('button').addEventListener('click', () => {
            const menu = document.createElement('div');
            menu.setAttribute('role', 'menu');
            menu.innerHTML = `<span>${account}</span><button>Sign out</button>`;
            document.body.append(menu);
          });
        }""",
        {"account": ACCOUNT},
    )
    opened = page.evaluate(status_script(open_profile=True))
    assert opened["account_verified"] is False
    page.wait_for_timeout(25)
    verified = page.evaluate(status_script())
    assert verified["top_right_profile_control_count"] == 1
    assert verified["account_verified"] is True
    assert ACCOUNT not in json.dumps(verified)


def test_account_surface_can_be_linked_by_profile_control_aria_controls(page):
    page.set_content(
        f"""<header role="banner">
          <button aria-label="My account" aria-controls="popup-contents"></button>
        </header>
        <main role="main"></main>
        <section id="popup-contents" hidden>
          <span>{ACCOUNT}</span><button>Sign out</button>
        </section>"""
    )
    page.evaluate(
        """() => document.querySelector('button').addEventListener('click', () => {
          document.querySelector('#popup-contents').hidden = false;
        })"""
    )
    opened = page.evaluate(status_script(open_profile=True))
    assert opened["account_verified"] is False
    page.wait_for_timeout(25)
    verified = page.evaluate(status_script())
    assert verified["profile_control_controls_menu_count"] == 1
    assert verified["account_verified"] is True


def test_unique_microsoft_account_control_is_used_when_other_profile_control_exists(page):
    page.set_content(
        f"""<header role="banner">
          <button id="O365_MainLink_Me" aria-controls="account-menu"></button>
          <button aria-label="Account settings"></button>
        </header>
        <main role="main"></main>
        <div id="account-menu" hidden>
          <span>{ACCOUNT}</span><button>Sign out</button>
        </div>"""
    )
    page.evaluate(
        """() => {
          document.querySelector('#O365_MainLink_Me').addEventListener('click', () => {
            document.querySelector('#account-menu').hidden = false;
          });
          document.querySelector('[aria-label="Account settings"]').addEventListener(
            'click', () => { throw new Error('wrong profile control clicked'); });
        }"""
    )
    opened = page.evaluate(status_script(open_profile=True))
    assert opened["profile_control_count"] == 2
    assert opened["profile_control_trigger_count"] == 1
    page.wait_for_timeout(25)
    verified = page.evaluate(status_script())
    assert verified["account_verified"] is True


def test_status_tracks_outlook_frames_and_never_reports_account_text():
    assert "core.FrameCreated += (s, e) => TrackFrame(e.Frame);" in SOURCE
    assert "frame.FrameCreated += (s, e) => TrackFrame(e.Frame);" in SOURCE
    assert "Json.Deserialize<string>(" in SOURCE
    assert "IsOutlookAppHost(frameHost)" in SOURCE
    assert "account_menu_email_count" in SOURCE
    assert '"open_shadow_root_count"' in SOURCE
    assert '"top_right_profile_control_count"' in SOURCE
    assert '"profile_control_controls_menu_count"' in SOURCE
    assert '"profile_control_trigger_count"' in SOURCE
