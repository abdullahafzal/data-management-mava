"""Detect a closed Chrome session and recreate the Selenium driver."""

from __future__ import annotations

from typing import Any, Callable

from selenium.common.exceptions import WebDriverException

BROWSER_CLOSED_HINTS = (
    'invalid session id',
    'invalid session',
    'disconnected',
    'not connected to devtools',
    'target window already closed',
    'no such window',
    'chrome not reachable',
    'session deleted',
    'browser has closed',
    'unable to receive message from renderer',
    'failed to check if window',
)


def is_driver_alive(driver: Any) -> bool:
    if driver is None:
        return False
    try:
        handles = driver.window_handles
        return bool(handles)
    except WebDriverException:
        return False
    except Exception:
        return False


def looks_like_browser_closed(exc: BaseException | None = None, message: str = '') -> bool:
    text = message or str(exc or '').lower()
    return any(h in text for h in BROWSER_CLOSED_HINTS)


def safe_quit_driver(driver: Any) -> None:
    if driver is None:
        return
    try:
        driver.quit()
    except Exception:
        pass


def recreate_driver(*, headless: bool, build_driver: Callable[..., Any], log: Callable[[str], None] | None = None):
    if log:
        log('[Job] Chrome window was closed — opening a new browser.\n')
    return build_driver(headless=headless)


def result_indicates_browser_closed(results: list[Any]) -> bool:
    for row in results:
        err = getattr(row, 'error', None) or ''
        if looks_like_browser_closed(message=str(err)):
            return True
    return False
