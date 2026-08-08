from __future__ import annotations


def install_naruto_game() -> None:
    """Lazy integration entrypoint used by app.main."""
    from .integration import install_naruto_game as _install

    _install()


__all__ = ["install_naruto_game"]
