"""simular_sem_anexos so existe em dry-run (10/10)."""
import pytest

import esteira


def test_simulacao_recusa_escrita_real():
    with pytest.raises(ValueError, match="dry_run"):
        esteira.rodar_esteira("01/10/2026", dry_run=False, simular_sem_anexos=True,
                              conta="397950")
