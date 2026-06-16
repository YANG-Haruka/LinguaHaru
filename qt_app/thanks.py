"""A small 'thank you for using LinguaHaru' dialog shown when an experience
finishes (document translation done, real-time voice stopped), summarizing the
tokens used + estimated cost. Mirrors the Web showThanks() card."""
from qfluentwidgets import MessageBox

from qt_app.i18n import tr


def _fmt_tokens(n):
    n = int(n or 0)
    if n >= 1000:
        return f"{n / 1000:.0f}K" if n >= 10000 else f"{n / 1000:.1f}K"
    return str(n)


def show_thanks(parent, lang, tokens, cost_amount=None, cost_symbol=None, cost_currency=None):
    """Show the thanks dialog when a LONG task finishes. Always thanks the user;
    the tokens / cost lines appear only when known (offline/local/PDF runs may
    report none). Not used for high-frequency Quick Translate."""
    lines = []
    if tokens:
        lines.append(f"{tr('Thanks Tokens Label', lang)}: {_fmt_tokens(tokens)} tokens")
    if cost_amount is not None:
        lines.append(f"{tr('Thanks Cost Label', lang)}: {cost_symbol}{cost_amount} {cost_currency}")
    try:
        box = MessageBox(tr("Thanks Title", lang), "\n".join(lines), parent)
        box.yesButton.setText(tr("OK", lang))
        box.cancelButton.hide()
        box.exec()
    except Exception:  # noqa: BLE001 — a thank-you must never break the flow
        pass
