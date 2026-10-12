"""A small 'thank you for using LinguaHaru' dialog shown when a LONG task
finishes (document translation done, real-time voice stopped), summarizing the
tokens used + estimated cost. Mirrors the Web showThanks() card.

Skipped when there are no tokens to report, and throttled (at most once a day,
across restarts) so frequent runs don't pop a dialog every time."""
import time

from qfluentwidgets import MessageBox

from qt_app.i18n import tr

_COOLDOWN_S = 24 * 3600   # at most once a day (persisted: survives restarts)


def _fmt_tokens(n):
    n = int(n or 0)
    if n >= 1000:
        return f"{n / 1000:.0f}K" if n >= 10000 else f"{n / 1000:.1f}K"
    return str(n)


def show_thanks(parent, lang, tokens):
    """Show the thanks dialog when a LONG task finishes. Skipped when there are
    no tokens to report, and throttled to once per _COOLDOWN_S. Not used for
    high-frequency Quick Translate."""
    if not tokens:
        return
    from core import backend
    now = time.time()
    try:
        if now - float(backend.get_config("thanks_last_shown", 0) or 0) < _COOLDOWN_S:
            return
        backend.set_config("thanks_last_shown", now)
    except (TypeError, ValueError):
        pass
    lines = [f"{tr('Thanks Tokens Label', lang)}: {_fmt_tokens(tokens)} tokens"]
    try:
        box = MessageBox(tr("Thanks Title", lang), "\n".join(lines), parent)
        box.yesButton.setText(tr("OK", lang))
        box.cancelButton.hide()
        box.exec()
    except Exception:  # noqa: BLE001 — a thank-you must never break the flow
        pass
