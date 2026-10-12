"""Rodada real sem chave do Gemini aborta com causa clara (dono avisado) — 11/10."""
import pytest

import esteira


def test_rodada_real_sem_gemini_aborta():
    with pytest.raises(RuntimeError, match="Gemini"):
        esteira.rodar_esteira("01/10/2026", gemini_key=None, k_attach=1, dry_run=False,
                              conta="397950")
