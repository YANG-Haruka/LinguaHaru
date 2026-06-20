"""First-run onboarding tutorial (welcome modal + multi-step carousel).

Mirrors the Web onboarding (webapp/static: #onboard-modal). Shown once, gated by
config key ``onboarding_seen``; the Web end uses per-browser localStorage instead
(same behaviour, platform-appropriate mechanism)."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel
from qfluentwidgets import (
    TitleLabel, BodyLabel, PrimaryPushButton, PushButton, TransparentPushButton,
)

from core import backend
from qt_app.i18n import tr

# (emoji, title-key, body-key) — same five steps + copy as the Web carousel.
_STEPS = [
    ("✨", "Onboarding T1 Title", "Onboarding T1 Body"),
    ("🔑", "Onboarding T2 Title", "Onboarding T2 Body"),
    ("📄", "Onboarding T3 Title", "Onboarding T3 Body"),
    ("🎙️", "Onboarding T4 Title", "Onboarding T4 Body"),
    ("📚", "Onboarding T5 Title", "Onboarding T5 Body"),
]


class OnboardingDialog(QDialog):
    def __init__(self, lang, parent=None):
        super().__init__(parent)
        self._lang = lang
        self._step = 0
        self.setModal(True)
        self.setWindowTitle("LinguaHaru")
        self.setFixedWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 26, 28, 22)
        root.setSpacing(14)

        self._icon = QLabel(_STEPS[0][0])
        f = self._icon.font(); f.setPointSize(34); self._icon.setFont(f)
        self._icon.setAlignment(Qt.AlignCenter)
        root.addWidget(self._icon)

        self._title = TitleLabel("")
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setWordWrap(True)
        root.addWidget(self._title)

        self._body = BodyLabel("")
        self._body.setWordWrap(True)
        self._body.setMinimumHeight(96)
        self._body.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        root.addWidget(self._body)

        self._dots = QLabel("")
        self._dots.setAlignment(Qt.AlignCenter)
        root.addWidget(self._dots)

        row = QHBoxLayout()
        self._skip = TransparentPushButton(tr("Onboarding Skip", lang))
        self._skip.clicked.connect(self._finish)
        self._back = PushButton(tr("Onboarding Back", lang))
        self._back.clicked.connect(self._go_back)
        self._next = PrimaryPushButton(tr("Onboarding Next", lang))
        self._next.clicked.connect(self._go_next)
        row.addWidget(self._skip)
        row.addStretch(1)
        row.addWidget(self._back)
        row.addWidget(self._next)
        root.addLayout(row)

        self._render()

    def _render(self):
        icon, tkey, bkey = _STEPS[self._step]
        self._icon.setText(icon)
        self._title.setText(tr(tkey, self._lang))
        self._body.setText(tr(bkey, self._lang))
        self._back.setVisible(self._step > 0)
        last = self._step == len(_STEPS) - 1
        self._next.setText(tr("Onboarding Done" if last else "Onboarding Next", self._lang))
        self._dots.setText("  ".join(
            "●" if i == self._step else "○" for i in range(len(_STEPS))))

    def _go_back(self):
        if self._step > 0:
            self._step -= 1
            self._render()

    def _go_next(self):
        if self._step < len(_STEPS) - 1:
            self._step += 1
            self._render()
        else:
            self._finish()

    def _finish(self):
        self.accept()


def maybe_show_onboarding(parent, lang):
    """Show the tutorial once per install. No-op if already seen."""
    if backend.get_config("onboarding_seen", False):
        return
    try:
        OnboardingDialog(lang, parent).exec()
    finally:
        backend.set_config("onboarding_seen", True)
