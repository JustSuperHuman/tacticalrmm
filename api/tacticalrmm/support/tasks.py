from tacticalrmm.celery import app

from . import screens


@app.task(ignore_result=True)
def capture_screen(device):
    """Take a screen preview of a support computer and keep it in the cache."""
    screens.capture(device)
