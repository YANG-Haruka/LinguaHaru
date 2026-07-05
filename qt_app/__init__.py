"""Native Qt (Fluent Design) desktop frontend for LinguaHaru.

This package reuses LinguaHaru's translation backend (the translator classes
and config modules) WITHOUT importing app.py, so Gradio is never pulled in.
The entry point is app_qt.py at the repo root.
"""
