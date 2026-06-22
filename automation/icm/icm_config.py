"""Apply Django settings to ICM Selenium modules before launching Chrome."""

from __future__ import annotations

from django.conf import settings


def _set(module, name: str, value) -> None:
    if value is not None and value != '':
        setattr(module, name, value)


def apply_django_settings() -> None:
    from automation.icm import icm_name_search_automation as name_search
    from automation.icm import second_site_automation as ssa

    headless = getattr(settings, 'ICM_HEADLESS', False)
    ssa.HEADLESS = headless
    name_search.HEADLESS = headless

    pause = getattr(settings, 'ICM_PAUSE_BETWEEN_SEARCHES', None)
    if pause is not None:
        name_search.PAUSE_BETWEEN_SEARCHES = float(pause)

    _set(ssa, 'USE_REMOTE_DEBUGGING', getattr(settings, 'ICM_USE_REMOTE_DEBUGGING', False))
    _set(ssa, 'REMOTE_DEBUGGING_ADDRESS', getattr(settings, 'ICM_REMOTE_DEBUGGING_ADDRESS', ''))
    _set(ssa, 'CHROME_USER_DATA_DIR', getattr(settings, 'ICM_CHROME_USER_DATA_DIR', ''))
    _set(ssa, 'CHROME_PROFILE_DIRECTORY', getattr(settings, 'ICM_CHROME_PROFILE_DIRECTORY', ''))
    _set(
        ssa,
        'CHROME_PROFILE_EXACT_DISPLAY_NAME',
        getattr(settings, 'ICM_CHROME_PROFILE_NAME', ''),
    )
    _set(ssa, 'CHROME_PROFILE_EMAIL', getattr(settings, 'ICM_CHROME_PROFILE_EMAIL', ''))
    _set(ssa, 'USE_CHROME_PROFILE', getattr(settings, 'ICM_USE_CHROME_PROFILE', True))

    login_email = getattr(settings, 'ICM_LOGIN_EMAIL', '')
    login_password = getattr(settings, 'ICM_LOGIN_PASSWORD', '')
    if login_email:
        ssa.ICM_LOGIN_EMAIL = login_email
    if login_password:
        ssa.ICM_LOGIN_PASSWORD = login_password
