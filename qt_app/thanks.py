"""A small 'thank you for using LinguaHaru' dialog shown when a LONG task
finishes (document translation done, real-time voice stopped), summarizing the
tokens used + estimated cost. Mirrors the Web showThanks() card.

Skipped when there are no tokens to report, and throttled (at most once per
_COOLDOWN_S) so frequent runs don't pop a dialog every time."""
import time

from qfluentwidgets import MessageBox

from qt_app.i18n import tr

_COOLDOWN_S = 10 * 60   # pop the dialog at most once per 10 minutes
_last_shown = 0.0


def _fmt_tokens(n):
    n = int(n or 0)
    if n >= 1000:
        return f"{n / 1000:.0f}K" if n >= 10000 else f"{n / 1000:.1f}K"
    return str(n)


def show_thanks(parent, lang, tokens, cost_amount=None, cost_symbol=None, cost_currency=None):
    """Show the thanks dialog when a LONG task finishes. Skipped when there are
    no tokens to report, and throttled to once per _COOLDOWN_S. Not used for
    high-frequency Quick Translate."""
    global _last_shown
    if not tokens:
        return
    now = time.time()
    if now - _last_shown < _COOLDOWN_S:   # within cooldown -> skip
        return
    _last_shown = now
    lines = [f"{tr('Thanks Tokens Label', lang)}: {_fmt_tokens(tokens)} tokens"]
    if cost_amount is not None:
        lines.append(f"{tr('Thanks Cost Label', lang)}: {cost_symbol}{cost_amount} {cost_currency}")
    try:
        box = MessageBox(tr("Thanks Title", lang), "\n".join(lines), parent)
        box.yesButton.setText(tr("OK", lang))
        box.cancelButton.hide()
        box.exec()
    except Exception:  # noqa: BLE001 — a thank-you must never break the flow
        pass
