"""Shared headless Chrome setup for browser automations."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

if TYPE_CHECKING:
    from selenium.webdriver.chrome.webdriver import WebDriver


def headless_default() -> bool:
    raw = os.environ.get('AUTOMATION_HEADLESS_CHROME', 'true').strip().lower()
    return raw not in ('0', 'false', 'no', 'off')


def build_chrome_driver(*, headless: bool | None = None) -> WebDriver:
    """Launch Chrome for Selenium. Headless by default (AUTOMATION_HEADLESS_CHROME)."""
    if headless is None:
        headless = headless_default()

    options = Options()
    if headless:
        options.add_argument('--headless=new')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-gpu')
    else:
        options.add_argument('--start-maximized')

    options.add_argument('--disable-blink-features=AutomationControlled')
    options.add_argument('--no-first-run')
    options.add_argument('--no-default-browser-check')
    options.add_argument('--disable-dev-shm-usage')

    chrome_bin = os.environ.get('CHROME_BINARY', '').strip()
    if chrome_bin:
        options.binary_location = chrome_bin

    try:
        options.page_load_strategy = 'eager'
    except Exception:
        pass

    return webdriver.Chrome(options=options)
