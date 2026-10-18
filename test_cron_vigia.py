"""Vigia do cron (18/10): rodada travada não pode prender as próximas para sempre."""
import threading


def test_cron_travado_limite():
    import app as a
    assert not a._cron_travado(100.0, None, 10)
    assert not a._cron_travado(100.0, 95.0, 10)
    assert a._cron_travado(100.0, 80.0, 10)


def test_rodada_travada_e_liberada(monkeypatch):
    import app as a
    monkeypatch.setattr(a, "_CRON_TRAVADO_S", 0)
    avisos = []
    import notificador
    monkeypatch.setattr(notificador, "enviar_whatsapp", lambda t, **k: avisos.append(t) or True)
    rodou = []
    monkeypatch.setattr(a, "_faturar_cron_body", lambda: rodou.append(1))
    a._faturar_cron_running.set()                 # simula rodada antiga presa
    a._cron_estado.update(inicio=a.time.monotonic() - 5, avisado=None)
    a._faturar_cron_rodar()
    assert rodou == [1] and len(avisos) == 1
    assert not a._faturar_cron_running.is_set()


def test_rodada_em_andamento_nao_duplica(monkeypatch):
    import app as a
    monkeypatch.setattr(a, "_CRON_TRAVADO_S", 3600)
    rodou = []
    monkeypatch.setattr(a, "_faturar_cron_body", lambda: rodou.append(1))
    a._faturar_cron_running.set()
    a._cron_estado.update(inicio=a.time.monotonic())
    try:
        a._faturar_cron_rodar()
        assert rodou == []
    finally:
        a._faturar_cron_running.clear()
        a._cron_estado.update(inicio=None)


def test_travada_que_acorda_nao_solta_trava_da_nova(monkeypatch):
    import app as a
    monkeypatch.setattr(a, "_CRON_TRAVADO_S", 0)
    import notificador
    monkeypatch.setattr(notificador, "enviar_whatsapp", lambda t, **k: True)
    solta_velha, velha_entrou, nova_rodando, fim_nova = (threading.Event() for _ in range(4))

    def corpo():
        if not velha_entrou.is_set():
            velha_entrou.set()
            solta_velha.wait(5)          # a velha fica presa
        else:
            nova_rodando.set()
            fim_nova.wait(5)

    monkeypatch.setattr(a, "_faturar_cron_body", corpo)
    a._faturar_cron_running.clear()
    a._cron_estado.update(inicio=None, avisado=None)
    t1 = threading.Thread(target=a._faturar_cron_rodar); t1.start()
    velha_entrou.wait(5)
    t2 = threading.Thread(target=a._faturar_cron_rodar); t2.start()
    nova_rodando.wait(5)
    solta_velha.set(); t1.join(5)                # velha acorda e termina
    assert a._faturar_cron_running.is_set()      # a nova continua protegida
    fim_nova.set(); t2.join(5)
    assert not a._faturar_cron_running.is_set()
